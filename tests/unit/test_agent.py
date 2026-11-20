"""Jarvis agents: chat actions, follow-ups, weekly agents and the scheduler."""

from datetime import date, datetime, timedelta

import pytest

from goal_tracker.assist.actions import looks_like_command
from goal_tracker.assist.followups import next_working_day, run_followups
from goal_tracker.assist.planning import WEEK_PLAN, completed_milestones
from goal_tracker.assist.proposals import ProposalStore, services_for
from goal_tracker.assist.store import SuggestionStore
from goal_tracker.assist.weekly import run_planning, run_wrapup
from goal_tracker.domain.goal_changes import GoalUpdate
from goal_tracker.domain.models import GoalCreate
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.llm.assist_schemas import AgentDecision, DayPlan, WeekPlan, WeekRecap
from goal_tracker.progress.workflow import load_all_pending, load_goal_lookup
from goal_tracker.web import scheduler


JSON = {"Accept": "application/json"}


def _decision(**values) -> AgentDecision:
    base = {"intent": "none", "target": "", "state": "none", "text": "", "minutes": -1, "date": ""}
    return AgentDecision(**{**base, **values})


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _goal(web, *, start=None, milestones=("Finish the exercise",), title="Learn LangChain", due=None):
    start = start or _monday(web.today)
    return web.goals.create_goal(
        GoalCreate.model_validate(
            {
                "period": {
                    "kind": "weekly",
                    "start_date": start.isoformat(),
                    "timezone": web.context.settings.timezone,
                },
                "title": title,
                "milestones": [
                    {"description": text, **({"due_date": due.isoformat()} if due else {})}
                    for text in milestones
                ],
            }
        )
    )["goal"]


def _pending(web, description="Set up the vector store", day=None):
    web.progress.record_progress(
        DailyProgressInput.model_validate(
            {
                "activity_date": (day or web.today).isoformat(),
                "raw_text": description,
                "activities": [{"description": description, "state": "pending"}],
            }
        )
    )


def _store(web) -> ProposalStore:
    return ProposalStore(web.paths.database)


def _ask(web, text):
    return web.client.post("/chat/ask", json={"question": text}).json()


# ----------------------------------------------------------- commands


@pytest.mark.parametrize(
    "text",
    [
        "Mark the vector store as done",
        "log 30 minutes of code review today",
        "Add milestone 'deploy' to my LangChain goal",
        "move the exercise milestone to friday",
        "I finished the tool-calling exercise",
        "the report is done",
    ],
)
def test_commands_are_recognised(text):
    assert looks_like_command(text)


@pytest.mark.parametrize("text", ["What is pending?", "What did I learn this week?", "How are my goals going?"])
def test_questions_are_not_commands(text):
    assert not looks_like_command(text)


def test_questions_skip_the_action_step(web):
    _goal(web)
    from goal_tracker.llm.assist_schemas import ChatAnswer

    web.gateway.chat_answer = ChatAnswer(answered=True, answer="One goal.", sources=["G1"])

    assert _ask(web, "What are my goals?")["answered"] is True
    assert "decide_action" not in web.gateway.calls


# --------------------------------------------------------- repair step


def _facts():
    from goal_tracker.assist.chat import Fact

    return {
        "P1": Fact("P1", "Still in progress: tool-calling exercise is still pending.", kind="pending",
                   ref={"type": "pending", "id": "a1", "description": "tool-calling exercise is still pending",
                        "state": "in_progress", "goal_ids": [], "recorded": "2026-11-13"}),
        "P2": Fact("P2", "Still pending: Set up the vector store.", kind="pending",
                   ref={"type": "pending", "id": "a2", "description": "Set up the vector store",
                        "state": "pending", "goal_ids": [], "recorded": "2026-11-13"}),
        "M1": Fact("M1", 'Milestone "Deploy the app" of goal "Ship": not done.', kind="milestone",
                   ref={"type": "milestone", "id": "m1", "description": "Deploy the app"}),
    }


def test_repair_fixes_a_missing_target_from_the_message():
    from goal_tracker.assist.actions import repair

    # The real model returned this for "Mark the tool-calling exercise as done".
    decision = _decision(intent="complete_milestone", target="NONE", state="completed", text="none")

    fixed = repair(decision, _facts(), "Mark the tool-calling exercise as done")

    assert (fixed.intent, fixed.target, fixed.state, fixed.text) == ("update_work_status", "P1", "completed", "")


def test_repair_switches_to_the_milestone_the_message_names():
    from goal_tracker.assist.actions import repair

    fixed = repair(_decision(intent="update_work_status", target="", state="completed"), _facts(), "mark deploy the app done")

    assert (fixed.intent, fixed.target) == ("complete_milestone", "M1")


def test_repair_leaves_unclear_messages_alone():
    from goal_tracker.assist.actions import repair

    decision = _decision(intent="update_work_status", target="", state="completed")

    assert repair(decision, _facts(), "mark it as done").target == ""


# ------------------------------------------------------- chat actions


def test_status_update_needs_confirmation_and_runs_once(web):
    _pending(web)
    web.gateway.decision = _decision(intent="update_work_status", target="P1", state="completed")

    reply = _ask(web, "Mark the vector store as done")

    proposal = reply["proposal"]
    assert proposal["status"] == "pending"
    assert "Set up the vector store" in proposal["summary"]
    assert len(load_all_pending(web.progress, web.today)) == 1  # nothing saved yet

    for _ in range(2):  # a double click saves once
        outcome = web.client.post(f"/agent/proposals/{proposal['id']}/confirm", headers=JSON).json()
        assert outcome["status"] == "confirmed"

    assert load_all_pending(web.progress, web.today) == []
    entries = web.progress.get_daily_progress(web.today, web.today, limit=50, offset=0)
    assert len(entries) == 2  # the original entry and one status update


def test_dismissed_proposal_changes_nothing(web):
    _pending(web)
    web.gateway.decision = _decision(intent="update_work_status", target="P1", state="completed")
    proposal = _ask(web, "Mark the vector store as done")["proposal"]

    outcome = web.client.post(f"/agent/proposals/{proposal['id']}/dismiss", headers=JSON).json()

    assert outcome["status"] == "dismissed"
    assert len(load_all_pending(web.progress, web.today)) == 1
    # A dismissed proposal can no longer be confirmed.
    again = web.client.post(f"/agent/proposals/{proposal['id']}/confirm", headers=JSON).json()
    assert again["status"] == "dismissed"


@pytest.mark.parametrize(
    ("decision", "message"),
    [
        ({"intent": "update_work_status", "target": "P9", "state": "completed"}, "couldn't match"),
        ({"intent": "update_work_status", "target": "G1", "state": "completed"}, "couldn't match"),
        ({"intent": "log_work", "text": "Code review", "date": "2999-01-01"}, "today or earlier"),
        ({"intent": "add_milestone", "target": "G1", "text": ""}, "milestone to add"),
        ({"intent": "move_milestone", "target": "M1", "date": "2999-01-01"}, "within the goal's period"),
    ],
)
def test_invalid_actions_are_refused(web, decision, message):
    _goal(web)
    _pending(web)
    web.gateway.decision = _decision(**decision)

    reply = _ask(web, "Mark something done")

    assert reply["answered"] is False
    assert message in reply["answer"]
    assert reply["proposal"] is None
    assert _store(web).count_pending() == 0


def test_log_work_without_a_goal_becomes_an_independent_task(web):
    web.gateway.decision = _decision(intent="log_work", text="Code review", minutes=30, state="completed")
    _goal(web)  # gives Jarvis some records

    proposal = _ask(web, "Log 30 minutes of code review today")["proposal"]
    assert ["Goal", "Independent task"] in proposal["details"]

    web.client.post(f"/agent/proposals/{proposal['id']}/confirm", headers=JSON)

    [entry] = [
        e for e in web.progress.get_daily_progress(web.today, web.today, limit=50, offset=0)
        if e["activities"][0]["description"] == "Code review"
    ]
    activity = entry["activities"][0]
    assert activity["minutes"] == 30
    goals = load_goal_lookup(web.goals)
    assert [goals[g]["is_independent"] for g in activity["goal_ids"]] == [True]


def test_complete_and_add_milestone_through_chat(web):
    goal = _goal(web)

    web.gateway.decision = _decision(intent="complete_milestone", target="M1")
    done = _ask(web, "Mark the exercise milestone as done")["proposal"]
    web.client.post(f"/agent/proposals/{done['id']}/confirm", headers=JSON)

    services = services_for(web.context)
    current = web.goals.get_goal(goal["id"])
    assert completed_milestones(services.analysis, [current], web.today) == {current["milestones"][0]["id"]}

    web.gateway.decision = _decision(intent="add_milestone", target="G1", text="Deploy the demo")
    added = _ask(web, "Add milestone deploy the demo to my goal")["proposal"]
    outcome = web.client.post(f"/agent/proposals/{added['id']}/confirm", headers=JSON).json()

    assert outcome["status"] == "confirmed"
    assert "Deploy the demo" in [m["description"] for m in web.goals.get_goal(goal["id"])["milestones"]]


def test_conflicting_goal_change_marks_the_proposal_failed(web):
    goal = _goal(web)
    web.gateway.decision = _decision(intent="add_milestone", target="G1", text="Deploy the demo")
    proposal = _ask(web, "Add milestone deploy the demo")["proposal"]

    # The goal changes after the proposal was made.
    web.goals.update_goal(
        GoalUpdate.model_validate(
            {"goal_id": goal["id"], "expected_revision": 1, "reason": "Rename", "title": "Renamed"}
        )
    )
    outcome = web.client.post(f"/agent/proposals/{proposal['id']}/confirm", headers=JSON).json()

    assert outcome["status"] == "failed"
    assert outcome["result"]


# ---------------------------------------------------------- follow-ups


def test_next_working_day_skips_weekends():
    assert next_working_day(date(2026, 11, 19)) == date(2026, 11, 20)  # Thu -> Fri
    assert next_working_day(date(2026, 11, 20)) == date(2026, 11, 23)  # Fri -> Mon
    assert next_working_day(date(2026, 11, 22)) == date(2026, 11, 23)  # Sun -> Mon


def test_stale_pending_work_gets_one_follow_up(web):
    _pending(web, "Old task", day=web.today - timedelta(days=4))
    _pending(web, "New task")
    services, store = services_for(web.context), _store(web)

    assert run_followups(services, store, web.today) == 1
    assert run_followups(services, store, web.today) == 0  # not repeated

    [proposal] = store.by_status("pending")
    assert proposal["kind"] == "update_work_status"
    assert "Old task" in proposal["summary"]


def test_overdue_milestone_gets_done_and_move_suggestions(web):
    # Use next week so the test does not depend on today's weekday:
    # the milestone is due Monday and "today" is that Wednesday.
    next_monday = _monday(web.today) + timedelta(days=7)
    wednesday = next_monday + timedelta(days=2)
    _goal(web, start=next_monday, due=next_monday)
    services, store = services_for(web.context), _store(web)

    run_followups(services, store, wednesday)

    proposals = store.by_status("pending")
    assert sorted(p["kind"] for p in proposals) == ["complete_milestone", "move_milestone"]
    move = next(p for p in proposals if p["kind"] == "move_milestone")
    assert move["payload"]["due_date"] == (wednesday + timedelta(days=1)).isoformat()


# ------------------------------------------------------- weekly agents


def test_wrapup_proposes_carry_over_and_recap(web):
    _goal(web, milestones=("Open item",))
    web.gateway.recap = WeekRecap(points=["You worked on LangChain."])
    services, store = services_for(web.context), _store(web)

    assert run_wrapup(services, store, web.gateway, web.today) == 2
    assert run_wrapup(services, store, web.gateway, web.today) == 0

    carry = next(p for p in store.by_status("pending") if p["kind"] == "create_goal")
    outcome = web.client.post(f"/agent/proposals/{carry['id']}/confirm", headers=JSON).json()
    assert outcome["status"] == "confirmed"

    next_monday = (_monday(web.today) + timedelta(days=7)).isoformat()
    created = [g for g in load_goal_lookup(web.goals).values() if g["period"]["start_date"] == next_monday]
    assert [g["title"] for g in created] == ["Learn LangChain (carried over)"]
    assert [m["description"] for m in created[0]["milestones"]] == ["Open item"]


def test_planning_builds_the_week_plan_once(web):
    from goal_tracker.assist.planning import week_to_plan

    monday, days = week_to_plan(web.today)
    _goal(web, start=monday)
    web.gateway.week_plan = WeekPlan(days=[DayPlan(day=d, focus="Focus", tasks=["Finish the exercise"]) for d in days], notes=[])
    services, store = services_for(web.context), _store(web)
    plans = SuggestionStore(web.paths.database)

    created = run_planning(services, store, web.gateway, plans, web.context.settings, web.today)

    assert created == 1
    assert plans.get(WEEK_PLAN, monday.isoformat()) is not None
    assert run_planning(services, store, web.gateway, plans, web.context.settings, web.today) == 0
    assert web.gateway.calls.count("plan_week") == 1


# ----------------------------------------------------------- scheduler


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 11, 16, 8, 0), []),                          # Monday early
        (datetime(2026, 11, 16, 10, 0), ["planning"]),               # Monday 10:00
        (datetime(2026, 11, 17, 19, 0), ["followups", "planning"]),  # Tuesday evening, catch-up
        (datetime(2026, 11, 19, 12, 0), []),                         # Thursday: too late to plan
        (datetime(2026, 11, 20, 17, 30), ["wrapup"]),                # Friday 17:30
        (datetime(2026, 11, 21, 12, 0), ["wrapup"]),                 # Saturday
    ],
)
def test_due_jobs(web, moment, expected):
    assert [job.name for job in scheduler.due_jobs(moment, _store(web))] == expected


def test_tick_runs_each_slot_once_and_retries_failures(web, monkeypatch):
    ran = []

    def fake_run(name, context, now=None, gateway=None):
        ran.append(name)
        if name == "planning":
            raise RuntimeError("Ollama down")
        return 0

    monkeypatch.setattr(scheduler, "run_job", fake_run)
    moment = datetime(2026, 11, 17, 19, 0)

    assert scheduler.tick(web.context, moment) == ["followups"]
    assert scheduler.tick(web.context, moment) == []  # followups done; planning retried
    assert ran == ["followups", "planning", "planning"]


# ------------------------------------------------------------ pages


def test_agent_page_lists_and_confirms_suggestions(web):
    _pending(web, "Old task", day=web.today - timedelta(days=4))

    run = web.client.post("/agent/run", data={"job": "followups"}, follow_redirects=False)
    assert run.status_code == 303

    page = web.client.get(run.headers["location"]).text
    assert "Follow-ups" in page
    assert "Old task" in page
    assert web.client.get("/agent/count").json() == {"pending": 1}
    assert "Jarvis has <strong>1</strong> suggestion" in web.client.get("/").text

    [proposal] = _store(web).by_status("pending")
    confirmed = web.client.post(f"/agent/proposals/{proposal['id']}/confirm", follow_redirects=False)
    assert confirmed.status_code == 303
    assert web.client.get("/agent/count").json() == {"pending": 0}
