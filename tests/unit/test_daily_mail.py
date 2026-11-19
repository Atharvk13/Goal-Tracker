from datetime import date
from pathlib import Path

from goal_tracker.mail.daily import (
    DEFAULT_TEMPLATE_PATH,
    PLACEHOLDERS,
    build_daily_context,
    load_template,
    render_daily_mail,
)

DAY = date(2026, 11, 19)

GOALS = {
    "g1": {"title": "Ship the web UI"},
    "g2": {"title": "Learn agents"},
    "ind": {"title": "Independent tasks", "is_independent": True},
}


def _activity(description, kind="activity", state="completed", minutes=None,
              goal_ids=(), **extra):
    return {
        "kind": kind,
        "description": description,
        "state": state if kind == "activity" else "not_applicable",
        "minutes": minutes,
        "goal_ids": list(goal_ids),
        **extra,
    }


ENTRIES = [
    {
        "raw_text": "Morning: built the mail page.\n",
        "activities": [
            _activity("Built the mail page", minutes=90, goal_ids=["g1"]),
            _activity("Wrote tests", state="in_progress", goal_ids=["g1", "g2"]),
            _activity("Answered emails", minutes=30, goal_ids=["ind"]),
            _activity("Fixed printer", state="pending"),
            _activity("Jinja macros are handy", kind="learning"),
            _activity("Flaky VPN", kind="challenge"),
        ],
    },
    {
        "raw_text": "Afternoon: follow-up.",
        "activities": [
            _activity("Reviewed PR", minutes=15, goal_ids=["g2"],
                      updates_activity_id="old-id"),
        ],
    },
]


def test_context_separates_goal_and_other_tasks():
    context = build_daily_context(DAY, ENTRIES, GOALS, week_completion=42.5)

    assert context["goal_tasks"] == (
        "Ship the web UI:\n"
        "  - Built the mail page (Completed, 90 min)\n"
        "  - Wrote tests (In progress)\n"
        "Learn agents:\n"
        "  - Wrote tests (In progress)\n"
        "  - Reviewed PR (status update) (Completed, 15 min)"
    )
    assert context["other_tasks"] == (
        "- Answered emails (Completed, 30 min)\n"
        "- Fixed printer (Pending)"
    )
    assert context["open_tasks"] == (
        "- Wrote tests (In progress)\n- Fixed printer (Pending)"
    )
    assert context["all_open_tasks"] == "- None"
    assert context["learnings"] == "- Jinja macros are handy"
    assert context["challenges"] == "- Flaky VPN"
    assert context["achievements"] == "- None"
    assert context["total_minutes"] == "135"
    assert context["total_hours"] == "2 h 15 min"
    assert context["weekday"] == "Thursday"
    assert context["date_long"] == "19 November 2026"
    assert context["week_completion"] == "42.5%"
    assert context["entry_count"] == "2"
    assert context["journal"] == (
        "Morning: built the mail page.\n\nAfternoon: follow-up."
    )
    assert set(context) == set(PLACEHOLDERS)


def test_plan_ahead_lists_all_open_work():
    open_work = [
        {"description": "Old pending task", "state": "pending", "goal_ids": []},
        {"description": "Ongoing feature", "state": "in_progress", "goal_ids": ["g1"]},
    ]

    context = build_daily_context(DAY, [], GOALS, open_work=open_work)

    assert context["all_open_tasks"] == (
        "- Old pending task (Pending)\n- Ongoing feature (In progress)"
    )


def test_empty_day_uses_none_markers():
    context = build_daily_context(DAY, [], GOALS)

    assert context["goal_tasks"] == "- None"
    assert context["other_tasks"] == "- None"
    assert context["total_hours"] == "Not recorded"
    assert context["week_completion"] == "N/A"


def test_render_extracts_subject_and_reports_unknown_placeholders():
    template = (
        "Subject: Update {{ date }}\r\n"
        "\r\n"
        "Done:\r\n{{goal_tasks}}\r\n"
        "Mood: {{mood}} {{mood}}\r\n"
    )

    mail = render_daily_mail(template, {"date": "2026-11-19", "goal_tasks": "- A"})

    assert mail.subject == "Update 2026-11-19"
    assert mail.body == "Done:\n- A\nMood: {{mood}} {{mood}}\n"
    assert mail.unknown_placeholders == ["mood"]


def test_template_without_subject_line():
    mail = render_daily_mail("Hello {{date}}", {"date": "x"})

    assert mail.subject == ""
    assert mail.body == "Hello x\n"


def test_bundled_template_uses_only_known_placeholders():
    text, path = load_template(None)
    mail = render_daily_mail(text, build_daily_context(DAY, ENTRIES, GOALS))

    assert path == DEFAULT_TEMPLATE_PATH
    assert mail.subject == "Daily Update – 19 November 2026"
    assert mail.body.startswith("Hi Arpan,")
    assert mail.unknown_placeholders == []


def test_custom_template_path(tmp_path: Path):
    custom = tmp_path / "mine.txt"
    custom.write_text("Subject: Mine\n\n{{other_tasks}}", encoding="utf-8")

    text, path = load_template(custom)

    assert path == custom
    assert render_daily_mail(text, {"other_tasks": "- B"}).body == "- B\n"
