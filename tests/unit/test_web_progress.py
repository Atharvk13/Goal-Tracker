from datetime import timedelta

import pytest

from conftest import TIMEZONE, input_values
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.llm.schemas import ProgressProposal


@pytest.fixture
def goal(web):
    monday = web.today - timedelta(days=web.today.weekday())
    return web.goals.create_goal(
        GoalCreate(
            period=PeriodSpec(kind="weekly", start_date=monday, timezone=TIMEZONE),
            title="Ship the web UI",
            milestones=[
                {"description": "Build progress pages"},
                {"description": "Write tests"},
            ],
        )
    )["goal"]


def _item(index=0, **fields):
    prefix = f"item-{index}-"
    data = {
        prefix + "include": "on",
        prefix + "kind": "activity",
        prefix + "description": "Built the progress pages",
        prefix + "state": "completed",
        prefix + "minutes": "45",
        prefix + "milestone_state": "done",
    }
    data.update({prefix + key: value for key, value in fields.items()})
    return data


def _submit(web, *, raw_text="Built the progress pages today.", source="manual",
            items=None, action="review", on=None):
    items = items or [_item()]
    data = {
        "activity_date": (on or web.today).isoformat(),
        "raw_text": raw_text,
        "source": source,
        "item_count": str(len(items)),
        "action": action,
    }
    for item in items:
        data.update(item)
    return web.client.post("/progress/items", data=data)


def _save(web, review, **extra):
    draft_json = input_values(review.text, "draft_json")[0]
    independent = input_values(review.text, "independent_ids")
    return web.client.post(
        "/progress/save",
        data={
            "draft_json": draft_json,
            "independent_ids": independent[0] if independent else "",
            **extra,
        },
        follow_redirects=False,
    )


def _independent_goals(web):
    return [g for g in web.goals.list_goals() if g["is_independent"]]


def test_manual_entry_is_reviewed_then_saved_once(web, goal):
    milestone = goal["milestones"][0]

    start = web.client.post(
        "/progress/start",
        data={
            "activity_date": web.today.isoformat(),
            "raw_text": "Built the progress pages today.",
            "method": "manual",
        },
    )
    assert start.status_code == 200
    assert "Ship the web UI" in start.text
    assert "Build progress pages" in start.text

    review = _submit(
        web,
        items=[_item(goal=goal["id"], milestone=milestone["id"])],
    )
    assert review.status_code == 200
    assert "Review your entry" in review.text
    assert web.progress.get_daily_progress(web.today, web.today) == []

    saved = _save(web, review)
    assert saved.status_code == 303

    entry = web.client.get(saved.headers["location"])
    assert "Progress saved." in entry.text
    assert "Built the progress pages" in entry.text

    entries = web.progress.get_daily_progress(web.today, web.today)
    assert len(entries) == 1
    change = entries[0]["activities"][0]["milestone_changes"][0]
    assert change == {"milestone_id": milestone["id"], "state": "done"}

    # Submitting the same draft again reuses its request ID.
    assert _save(web, review).status_code == 303
    assert len(web.progress.get_daily_progress(web.today, web.today)) == 1


def test_incomplete_work_records_milestones_as_not_done(web, goal):
    milestone = goal["milestones"][0]

    review = _submit(
        web,
        items=[_item(state="in_progress", goal=goal["id"], milestone=milestone["id"])],
    )
    _save(web, review)

    change = web.progress.get_daily_progress(web.today, web.today)[0][
        "activities"
    ][0]["milestone_changes"][0]
    assert change["state"] == "not_done"


def test_milestone_requires_a_linked_goal(web, goal):
    # No goal ticked makes the work independent, which cannot change milestones.
    response = _submit(
        web,
        items=[_item(milestone=goal["milestones"][0]["id"])],
    )

    assert response.status_code == 422
    assert "independent tasks cannot change milestones" in response.text


def test_add_item_keeps_existing_values(web):
    response = _submit(web, items=[_item(description="First item")], action="add_item")

    assert response.status_code == 200
    assert 'value="First item"' in response.text
    assert input_values(response.text, "item_count") == ["2"]


def test_excluding_every_item_is_rejected(web):
    item = _item()
    del item["item-0-include"]

    response = _submit(web, items=[item])

    assert response.status_code == 422
    assert "Include at least one item" in response.text


def test_duplicate_text_requires_explicit_confirmation(web):
    _save(web, _submit(web))

    review = _submit(web)
    assert "Matching entries already exist" in review.text
    assert input_values(review.text, "allow_duplicate_entry") == ["1"]

    blocked = _save(web, review)
    assert blocked.status_code == 409

    allowed = _save(web, review, allow_duplicate_entry="1")
    assert allowed.status_code == 303
    assert len(web.progress.get_daily_progress(web.today, web.today)) == 2


def test_ai_proposals_are_prefilled_with_quotes(web, goal):
    text = "Finished the review page. Learned that CRLF breaks quotes."
    web.gateway.proposal = ProgressProposal(
        activities=[
            {
                "kind": "activity",
                "description": "Finished the review page",
                "status": "completed",
                "evidence_quote": "Finished the review page.",
            },
            {
                "kind": "learning",
                "description": "CRLF breaks quotes",
                "status": "not_applicable",
                "evidence_quote": "Learned that CRLF breaks quotes.",
            },
        ]
    )

    editor = web.client.post(
        "/progress/start",
        data={
            "activity_date": web.today.isoformat(),
            "raw_text": text.replace(" Learned", "\r\nLearned"),
            "method": "ai",
        },
    )

    assert editor.status_code == 200
    assert "Review AI proposals" in editor.text
    assert input_values(editor.text, "source") == ["llm_reviewed"]

    learning = _item(1, kind="learning", description="CRLF breaks quotes",
                     quote="Learned that CRLF breaks quotes.")
    del learning["item-1-state"]
    review = _submit(
        web,
        raw_text=text.replace(" Learned", "\r\nLearned"),
        source="llm_reviewed",
        items=[
            _item(0, description="Finished the review page",
                  quote="Finished the review page.", goal=goal["id"]),
            learning,
        ],
    )

    assert review.status_code == 200, review.text
    assert _save(web, review).status_code == 303

    entry = web.progress.get_daily_progress(web.today, web.today)[0]
    assert entry["source"] == "llm_reviewed"
    assert [a["kind"] for a in entry["activities"]] == ["activity", "learning"]


def test_ai_failure_falls_back_to_manual_entry(web):
    web.gateway.error = ConnectionError("offline")

    response = web.client.post(
        "/progress/start",
        data={
            "activity_date": web.today.isoformat(),
            "raw_text": "Some work.",
            "method": "ai",
        },
    )

    assert response.status_code == 200
    assert "AI interpretation is unavailable" in response.text
    assert input_values(response.text, "source") == ["manual"]


def test_future_dates_and_blank_text_are_rejected(web):
    response = web.client.post(
        "/progress/start",
        data={
            "activity_date": (web.today + timedelta(days=1)).isoformat(),
            "raw_text": "   ",
            "method": "manual",
        },
    )

    assert response.status_code == 422
    assert "Choose today or an earlier date." in response.text
    assert "Please enter some journal text." in response.text


def test_pending_work_can_be_updated(web, goal):
    _save(
        web,
        _submit(web, items=[_item(state="pending", description="Write tests",
                                  goal=goal["id"])]),
    )

    pending_page = web.client.get("/progress/pending")
    activity = web.progress.list_pending_activities(as_of=web.today)[0]
    update_url = f"/progress/pending/{activity['id']}/update?on={web.today}"
    assert update_url in pending_page.text

    editor = web.client.get(update_url)
    assert editor.status_code == 200
    assert input_values(editor.text, "item-0-updates") == [activity["id"]]

    review = _submit(
        web,
        raw_text="Finished writing the tests.",
        items=[_item(description="Write tests", goal=goal["id"],
                     updates=activity["id"])],
    )
    assert review.status_code == 200, review.text
    assert _save(web, review).status_code == 303

    assert web.progress.list_pending_activities(as_of=web.today) == []


def test_progress_home_lists_entries(web):
    _save(web, _submit(web))

    page = web.client.get("/progress")

    assert page.status_code == 200
    assert web.today.isoformat() in page.text
    assert "Built the progress pages today." in page.text


def test_independent_task_uses_one_shared_goal(web, goal):
    # "Independent" wins even if a goal box was left ticked.
    review = _submit(
        web,
        items=[_item(description="Answered support emails",
                     related="independent", goal=goal["id"])],
    )
    assert review.status_code == 200, review.text
    assert "Independent task" in review.text
    assert _independent_goals(web) == []  # nothing created before confirming

    assert _save(web, review).status_code == 303
    independent = _independent_goals(web)
    assert len(independent) == 1
    assert independent[0]["title"] == "Independent tasks"

    entry = web.progress.get_daily_progress(web.today, web.today)[0]
    assert entry["activities"][0]["goal_ids"] == [independent[0]["id"]]

    # A second independent task (no goal ticked) reuses the same goal.
    _save(web, _submit(web, raw_text="More admin.",
                       items=[_item(description="Booked travel")]))
    assert len(_independent_goals(web)) == 1

    # The automatic goal is not offered in the goal picker.
    editor = web.client.post(
        "/progress/start",
        data={"activity_date": web.today.isoformat(), "raw_text": "x", "method": "manual"},
    )
    assert "Ship the web UI" in editor.text
    assert "Independent tasks</span>" not in editor.text


def test_editing_a_draft_keeps_the_independent_choice(web, goal):
    review = _submit(web, items=[_item(related="independent")])

    editor = web.client.post(
        "/progress/edit",
        data={
            "draft_json": input_values(review.text, "draft_json")[0],
            "independent_ids": input_values(review.text, "independent_ids")[0],
        },
    )

    assert editor.status_code == 200
    assert 'name="item-0-related" value="independent" checked' in editor.text


def test_independent_work_in_an_ended_week_is_saved_unlinked(web):
    last_week = web.today - timedelta(days=web.today.weekday() + 3)

    saved = _save(web, _submit(web, on=last_week, items=[_item(description="Old admin")]))

    assert saved.status_code == 303
    entry = web.progress.get_daily_progress(last_week, last_week)[0]
    assert entry["activities"][0]["goal_ids"] == []
    assert _independent_goals(web) == []


def test_independent_goal_is_badged_on_goal_pages(web):
    _save(web, _submit(web, items=[_item(description="Admin")]))
    goal_id = _independent_goals(web)[0]["id"]

    assert "badge-independent" in web.client.get("/goals").text
    assert "badge-independent" in web.client.get(f"/goals/{goal_id}").text


def test_daily_mail_groups_goal_and_other_tasks(web, goal):
    _save(
        web,
        _submit(
            web,
            raw_text="Built pages. Answered emails. Learned macros.",
            items=[
                _item(0, description="Built pages", goal=goal["id"]),
                _item(1, description="Answered emails", related="independent",
                      minutes="30"),
                {
                    "item-2-include": "on",
                    "item-2-kind": "learning",
                    "item-2-description": "Jinja macros",
                },
            ],
        ),
    )

    page = web.client.get(f"/progress/mail?on={web.today}")

    assert page.status_code == 200
    assert 'value="Daily Update – ' in page.text
    assert (
        "1. Meetings / Activities\n- Answered emails (Completed, 30 min)"
        in page.text
    )
    assert (
        "2. Use Cases / Activities Completed\n"
        "Ship the web UI:\n  - Built pages (Completed, 45 min)"
    ) in page.text
    assert "3. Learning\n- Jinja macros" in page.text
    assert "5. Blockers / Support Required\n- None" in page.text
    assert "Unknown placeholders" not in page.text


def test_daily_mail_plan_lists_all_open_work(web, goal):
    _save(web, _submit(web, raw_text="Started the API.",
                       items=[_item(description="Build the API", state="in_progress",
                                    goal=goal["id"])]))

    page = web.client.get(f"/progress/mail?on={web.today}")

    assert "4. Plan for Tomorrow\n- Build the API (In progress, 45 min)" in page.text


def test_daily_mail_uses_custom_template_and_flags_unknown(web, tmp_path):
    custom = tmp_path / "mine.txt"
    custom.write_text("Subject: Status {{date}}\n\n{{other_tasks}}\n{{mood}}",
                      encoding="utf-8")
    web.context.settings.daily_mail_template = custom

    page = web.client.get("/progress/mail")

    assert f'value="Status {web.today.isoformat()}"' in page.text
    assert "Unknown placeholders were left unchanged: {{mood}}" in page.text


def test_daily_mail_falls_back_when_template_missing(web, tmp_path):
    web.context.settings.daily_mail_template = tmp_path / "missing.txt"

    page = web.client.get("/progress/mail")

    assert page.status_code == 200
    assert "could not be read" in page.text
    assert "Hi Arpan," in page.text
