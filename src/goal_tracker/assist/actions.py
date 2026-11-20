"""Turn a Jarvis decision into a proposal, after strict checks.

The model only picks an intent and record IDs; everything is validated here
against the real records before a proposal is stored, and nothing is saved
until the user confirms it.
"""

import re
from datetime import date

from goal_tracker.assist.chat import Fact, words
from goal_tracker.assist.proposals import ids
from goal_tracker.llm.assist_schemas import AgentDecision


ACTION_INTENTS = {
    "update_work_status",
    "log_work",
    "complete_milestone",
    "add_milestone",
    "move_milestone",
}

# Messages that sound like a request for a change go to the action step;
# everything else goes straight to the answer step (one model call, not two).
_COMMAND = re.compile(
    r"^\s*(please\s+)?(mark|set|log|record|add|move|reschedule|postpone|push|"
    r"complete|finish|finished|update|change|change|create|note|i\s+(have\s+)?"
    r"(finished|completed|done|worked|spent))\b"
    r"|\b(as|is)\s+(done|complete|completed|finished|in progress)\b",
    re.IGNORECASE,
)


class ActionRefused(ValueError):
    """The decision cannot become a safe proposal."""


def looks_like_command(message: str) -> bool:
    return bool(_COMMAND.search(message))


ACTIONABLE_KINDS = ("goal", "milestone", "pending")


def _subject_words(fact: Fact) -> set[str]:
    ref = fact.ref
    return words(ref.get("description") or ref.get("title") or fact.text)


def match_record(message: str, facts: dict[str, Fact], types: tuple[str, ...]) -> Fact | None:
    """The one record the message clearly refers to, or None.

    Needs at least two shared words and a clear lead over the runner-up,
    so an unclear message never picks a record by chance.
    """
    asked = words(message)
    scored = sorted(
        (
            (len(asked & _subject_words(fact)), fact)
            for fact in facts.values()
            if fact.ref.get("type") in types
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not scored or scored[0][0] < 2:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def repair(decision: AgentDecision, facts: dict[str, Fact], message: str) -> AgentDecision:
    """Fix a missing or mismatched target from the message, deterministically.

    Small local models often pick the right kind of action but the wrong
    record ID (or none). This never invents anything: it only chooses among
    the supplied records, and the user still confirms the result.
    """
    target = facts.get(decision.target)
    target_type = target.ref.get("type") if target else None
    fixed = decision.model_copy()

    if decision.intent in ("update_work_status", "complete_milestone"):
        wanted = "pending" if decision.intent == "update_work_status" else "milestone"
        if target_type in ("pending", "milestone"):
            found = target
        elif target_type is None or target_type != wanted:
            found = match_record(message, facts, ("pending", "milestone"))
        else:
            found = target
        if found is not None:
            fixed.target = found.id
            if found.ref["type"] == "pending":
                fixed.intent = "update_work_status"
                if fixed.state not in ("completed", "in_progress", "pending"):
                    fixed.state = "completed"
            else:
                fixed.intent = "complete_milestone"

    elif decision.intent == "move_milestone" and target_type != "milestone":
        found = match_record(message, facts, ("milestone",))
        if found is not None:
            fixed.target = found.id

    elif decision.intent == "add_milestone" and target_type != "goal":
        found = match_record(message, facts, ("goal",))
        if found is not None:
            fixed.target = found.id

    elif decision.intent == "log_work" and decision.target and target_type != "goal":
        found = match_record(message, facts, ("goal",))
        fixed.target = found.id if found is not None else ""

    if fixed.text.strip().lower() == "none":
        fixed.text = ""
    return fixed


def _record(decision: AgentDecision, facts: dict[str, Fact], kind: str) -> dict:
    fact = facts.get(decision.target)
    if fact is None or fact.ref.get("type") != kind:
        names = {"goal": "goal", "milestone": "milestone", "pending": "pending work item"}
        raise ActionRefused(f"I couldn't match that to a {names[kind]} in your records.")
    return fact.ref


def _date(value: str, today: date) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ActionRefused("I couldn't understand that date.") from error


def build_proposal(decision: AgentDecision, facts: dict[str, Fact], today: date) -> dict:
    """Keyword arguments for ProposalStore.add, or ActionRefused."""
    intent = decision.intent

    if intent == "update_work_status":
        item = _record(decision, facts, "pending")
        if decision.state not in ("completed", "in_progress", "pending"):
            raise ActionRefused("Tell me the new status: completed or in progress.")
        if decision.state == item["state"]:
            raise ActionRefused(f"That work is already {item['state'].replace('_', ' ')}.")
        state = decision.state.replace("_", " ")
        return {
            "kind": "update_work_status",
            "summary": f"Mark “{item['description']}” as {state}",
            "details": [("Work", item["description"]), ("New status", state), ("Recorded on", item["recorded"])],
            "payload": {
                **ids(),
                "activity_id": item["id"],
                "description": item["description"],
                "state": decision.state,
                "goal_ids": item["goal_ids"],
            },
        }

    if intent == "log_work":
        text = decision.text.strip()
        if not text:
            raise ActionRefused("Tell me what work to log.")
        day = _date(decision.date, today) or today
        if day > today:
            raise ActionRefused("Work can only be logged for today or earlier.")
        goal = None
        if decision.target:
            goal = _record(decision, facts, "goal")
            if not goal["active"] or not (goal["period_start"] <= day.isoformat() <= goal["period_end"]):
                raise ActionRefused(f"The goal “{goal['title']}” doesn't accept work on {day.isoformat()}.")
        state = decision.state if decision.state in ("completed", "in_progress", "pending") else "completed"
        minutes = decision.minutes if decision.minutes >= 0 else None
        details = [("Work", text), ("Date", day.isoformat()), ("Status", state.replace("_", " "))]
        if minutes is not None:
            details.append(("Time", f"{minutes} min"))
        details.append(("Goal", goal["title"] if goal else "Independent task"))
        return {
            "kind": "log_work",
            "summary": f"Log “{text}” on {day.isoformat()}",
            "details": details,
            "payload": {
                **ids(),
                "activity_date": day.isoformat(),
                "description": text,
                "state": state,
                "minutes": minutes,
                "goal_ids": [goal["id"]] if goal else [],
            },
        }

    if intent == "complete_milestone":
        milestone = _record(decision, facts, "milestone")
        if milestone["done"]:
            raise ActionRefused("That milestone is already done.")
        if not milestone["active"]:
            raise ActionRefused("That milestone's goal is no longer active.")
        if not (milestone["period_start"] <= today.isoformat() <= milestone["period_end"]):
            raise ActionRefused("That milestone's goal period is not open today.")
        return {
            "kind": "complete_milestone",
            "summary": f"Mark milestone “{milestone['description']}” done",
            "details": [("Milestone", milestone["description"]), ("Goal", milestone["goal_title"])],
            "payload": {
                **ids(),
                "activity_date": today.isoformat(),
                "milestone_id": milestone["id"],
                "milestone": milestone["description"],
                "goal_id": milestone["goal_id"],
            },
        }

    if intent == "add_milestone":
        goal = _record(decision, facts, "goal")
        text = decision.text.strip()
        if not text:
            raise ActionRefused("Tell me the milestone to add.")
        if not goal["active"] or goal["independent"]:
            raise ActionRefused("Milestones can only be added to an active planned goal.")
        return {
            "kind": "add_milestone",
            "summary": f"Add milestone “{text}” to “{goal['title']}”",
            "details": [("Goal", goal["title"]), ("New milestone", text)],
            "payload": {
                **ids(),
                "goal_id": goal["id"],
                "expected_revision": goal["revision"],
                "description": text,
            },
        }

    if intent == "move_milestone":
        milestone = _record(decision, facts, "milestone")
        new_date = _date(decision.date, today)
        if new_date is None:
            raise ActionRefused("Tell me the new due date.")
        if milestone["done"]:
            raise ActionRefused("That milestone is already done.")
        if not (milestone["period_start"] <= new_date.isoformat() <= milestone["period_end"]):
            raise ActionRefused(
                f"The new date must be within the goal's period "
                f"({milestone['period_start']} to {milestone['period_end']})."
            )
        if new_date.isoformat() == milestone["due"]:
            raise ActionRefused("That milestone is already due on that date.")
        return {
            "kind": "move_milestone",
            "summary": f"Move “{milestone['description']}” to {new_date.isoformat()}",
            "details": [
                ("Milestone", milestone["description"]),
                ("Goal", milestone["goal_title"]),
                ("Due date", f"{milestone['due']} → {new_date.isoformat()}"),
            ],
            "payload": {
                **ids(),
                "goal_id": milestone["goal_id"],
                "expected_revision": milestone["goal_revision"],
                "milestone_id": milestone["id"],
                "due_date": new_date.isoformat(),
            },
        }

    raise ActionRefused("I can't do that from your records.")
