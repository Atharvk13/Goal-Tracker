"""Records for the chat assistant.

The assistant may only use facts taken from saved records. Each fact gets a
short ID (G1 goal, A1 journal item, P1 pending work) so answers can cite the
records they rely on, and the most relevant facts are chosen to fit the
local model's context.
"""

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from goal_tracker.assist.planning import completed_milestones
from goal_tracker.progress.workflow import load_all_pending, load_goal_lookup


HISTORY_DAYS = 120
FACT_BUDGET = 9000  # characters of facts sent to the model (~2-3k tokens)
NO_RECORDS_ANSWER = (
    "I couldn't find anything about that in your saved records, so I can't "
    "answer it. I only answer from your goals, progress and pending work."
)

_PAGE = 100
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    """a an and are as at be been but by can did do does done for from had has
    have how i if in into is it its me my of on or our so than that the their
    them then there these they this to was we were what when where which who
    why will with you your about any all also am ever get got give list show
    tell much many more most some please""".split()
)
_KIND = {
    "activity": "Work",
    "learning": "Learning",
    "challenge": "Challenge",
    "observation": "Observation",
    "achievement": "Achievement",
}


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    day: date | None = None
    kind: str = ""
    # What the fact points at, so Jarvis actions can target exact records.
    ref: dict = field(default_factory=dict, compare=False)


def words(text: str) -> set[str]:
    return {
        word[:-1] if len(word) > 4 and word.endswith("s") else word
        for word in _WORD.findall(text.lower())
        if word not in _STOPWORDS and len(word) > 1
    }


def _status(value: str) -> str:
    return value.replace("_", " ")


def _entries(progress, start: date, end: date) -> list[dict]:
    entries, offset = [], 0
    while True:
        page = progress.get_daily_progress(start, end, limit=_PAGE, offset=offset)
        entries.extend(page)
        if len(page) < _PAGE:
            return entries
        offset += _PAGE


def collect_facts(goal_service, analysis_service, progress_service, today: date) -> list[Fact]:
    """Every saved record the assistant may talk about, as short facts."""
    goals = load_goal_lookup(goal_service)
    done = completed_milestones(analysis_service, list(goals.values()), today)
    facts: list[Fact] = []

    for number, goal in enumerate(
        sorted(goals.values(), key=lambda g: g["period"]["start_date"], reverse=True),
        start=1,
    ):
        period = goal["period"]
        milestones = [
            f"{m['description']} ({'done' if m['id'] in done else 'not done'}, due {m['due_date']})"
            for m in goal["milestones"]
            if m["is_included"]
        ]
        parts = [
            f"{period['kind'].title()} goal \"{goal['title']}\"",
            f"period {period['start_date']} to {period['end_date']}",
            f"status {_status(goal['lifecycle_status'])}",
        ]
        if goal.get("is_independent"):
            parts.append("collects independent tasks, not scored")
        if goal["expected_outcome"]:
            parts.append(f"expected outcome: {goal['expected_outcome']}")
        if goal["description"]:
            parts.append(f"description: {goal['description']}")
        if milestones:
            parts.append(
                f"milestones {sum(m['id'] in done for m in goal['milestones'] if m['is_included'])}"
                f"/{len(milestones)} done: " + "; ".join(milestones)
            )
        facts.append(
            Fact(
                f"G{number}",
                ". ".join(parts) + ".",
                date.fromisoformat(period["start_date"]),
                "goal",
                {
                    "type": "goal",
                    "id": goal["id"],
                    "title": goal["title"],
                    "revision": goal["revision"],
                    "active": goal["lifecycle_status"] == "active",
                    "independent": bool(goal.get("is_independent")),
                    "period_start": period["start_date"],
                    "period_end": period["end_date"],
                },
            )
        )

    milestone_number = 0
    for goal in sorted(goals.values(), key=lambda g: g["period"]["start_date"], reverse=True):
        if goal.get("is_independent"):
            continue
        period = goal["period"]
        for milestone in goal["milestones"]:
            if not milestone["is_included"]:
                continue
            milestone_number += 1
            is_done = milestone["id"] in done
            facts.append(
                Fact(
                    f"M{milestone_number}",
                    f"Milestone \"{milestone['description']}\" of goal \"{goal['title']}\": "
                    f"{'done' if is_done else 'not done'}, due {milestone['due_date']}.",
                    date.fromisoformat(period["start_date"]),
                    "milestone",
                    {
                        "type": "milestone",
                        "id": milestone["id"],
                        "description": milestone["description"],
                        "done": is_done,
                        "due": milestone["due_date"],
                        "goal_id": goal["id"],
                        "goal_title": goal["title"],
                        "goal_revision": goal["revision"],
                        "active": goal["lifecycle_status"] == "active",
                        "period_start": period["start_date"],
                        "period_end": period["end_date"],
                    },
                )
            )

    titles = {goal_id: goal["title"] for goal_id, goal in goals.items()}
    start = today - timedelta(days=HISTORY_DAYS)
    number = 0
    for entry in sorted(_entries(progress_service, start, today), key=lambda e: e["activity_date"], reverse=True):
        for activity in entry["activities"]:
            number += 1
            text = f"{activity['activity_date']}: {_KIND.get(activity['kind'], activity['kind'])}: {activity['description']}"
            details = []
            if activity["kind"] == "activity" and activity["state"] != "not_applicable":
                details.append(_status(activity["state"]))
            if activity["minutes"] is not None:
                details.append(f"{activity['minutes']} min")
            linked = [titles[g] for g in activity["goal_ids"] if g in titles]
            if linked:
                details.append("goal: " + ", ".join(linked))
            if details:
                text += f" ({'; '.join(details)})"
            facts.append(
                Fact(f"A{number}", text + ".", date.fromisoformat(activity["activity_date"]), "journal")
            )

    for number, activity in enumerate(load_all_pending(progress_service, today), start=1):
        facts.append(
            Fact(
                f"P{number}",
                f"Still {_status(activity['state'])} as of {today.isoformat()}: "
                f"{activity['description']} (recorded {activity['activity_date']}).",
                date.fromisoformat(activity["activity_date"]),
                "pending",
                {
                    "type": "pending",
                    "id": activity["id"],
                    "description": activity["description"],
                    "state": activity["state"],
                    "goal_ids": list(activity["goal_ids"]),
                    "recorded": activity["activity_date"],
                },
            )
        )

    return facts


def _date_range(question: str, today: date) -> tuple[date, date] | None:
    """Dates the question refers to, if any."""
    text = question.lower()
    monday = today - timedelta(days=today.weekday())

    for found in re.findall(r"\d{4}-\d{2}-\d{2}", text):
        try:
            day = date.fromisoformat(found)
            return day, day
        except ValueError:
            pass
    if "yesterday" in text:
        day = today - timedelta(days=1)
        return day, day
    if "today" in text:
        return today, today
    if "last week" in text or "previous week" in text:
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if "this week" in text:
        return monday, today
    if "last month" in text:
        first = today.replace(day=1)
        previous_end = first - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    if "this month" in text:
        return today.replace(day=1), today
    return None


def select_facts(question: str, facts: list[Fact], today: date, budget: int = FACT_BUDGET) -> list[Fact]:
    """The most relevant facts for a question, within the context budget."""
    asked = words(question)
    window = _date_range(question, today)
    text = question.lower()
    wants = {
        "goal": bool(re.search(r"goal|milestone|plan|target|deadline|complet|progress|status", text)),
        "pending": bool(re.search(r"pending|open|left|remaining|todo|to do|unfinished|in progress", text)),
        "journal": bool(re.search(r"learn|challenge|block|achiev|work|did|done|spent|time|minute|hour|observ", text)),
    }

    def score(fact: Fact) -> float:
        value = 3.0 * len(asked & words(fact.text))
        if window and fact.day and window[0] <= fact.day <= window[1]:
            value += 6
        if wants.get("goal" if fact.kind == "milestone" else fact.kind):
            value += 2
        if fact.day:  # newer records first among equals
            value += max(0.0, 1 - (today - fact.day).days / HISTORY_DAYS)
        return value

    ranked = sorted(facts, key=score, reverse=True)
    if window:
        # A question about specific dates should not drift to other dates
        # unless nothing else matches.
        inside = [f for f in ranked if f.kind in ("goal", "milestone") or (f.day and window[0] <= f.day <= window[1])]
        ranked = inside + [f for f in ranked if f not in inside]

    chosen, used = [], 0
    for fact in ranked:
        if used + len(fact.text) > budget:
            continue
        chosen.append(fact)
        used += len(fact.text) + 8
    return chosen
