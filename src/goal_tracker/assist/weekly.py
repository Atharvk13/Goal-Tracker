"""Weekly agents: Friday wrap-up and Monday planning.

Both only prepare proposals (and a stored week plan, which is advice). Goals
and reports are created only when the user confirms the proposal.
"""

from datetime import date, timedelta
from uuid import uuid4

from goal_tracker.assist.chat import collect_facts
from goal_tracker.assist.planning import (
    NothingToPlan,
    WEEK_PLAN,
    completed_milestones,
    make_week_plan,
    week_to_plan,
)
from goal_tracker.assist.proposals import AgentServices, ProposalStore
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.progress.workflow import load_goal_lookup
from goal_tracker.reporting.advice import (
    completed_period_spec,
    gather_advice_evidence,
    model_provenance,
    request_recommendations,
)
from goal_tracker.reporting.periods import find_unreported_periods


CARRIED = " (carried over)"


def _goal_payload(period: PeriodSpec, title: str, milestones: list[str], *, description="", outcome="") -> dict:
    return {
        "goal": {
            "period": {
                "kind": period.kind.value,
                "start_date": period.start_date.isoformat(),
                "timezone": period.timezone,
            },
            "title": title[:200],
            "description": description,
            "expected_outcome": outcome,
            "milestones": [{"description": text} for text in milestones],
            "request_id": str(uuid4()),
        }
    }


def run_wrapup(services: AgentServices, store: ProposalStore, gateway, today: date) -> int:
    """Carry-over proposals for unfinished weekly goals, then a short recap."""
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    next_monday = monday + timedelta(days=7)
    next_week = PeriodSpec(kind="weekly", start_date=next_monday, timezone=services.timezone)
    created = 0

    goals = list(load_goal_lookup(services.goals).values())
    done = completed_milestones(services.analysis, goals, today)
    next_titles = {
        goal["title"].casefold()
        for goal in goals
        if goal["period"]["start_date"] == next_monday.isoformat()
    }

    for goal in goals:
        if (
            goal["period"]["kind"] != "weekly"
            or goal["period"]["start_date"] != monday.isoformat()
            or goal["lifecycle_status"] != "active"
            or goal.get("is_independent")
        ):
            continue
        open_milestones = [
            m["description"]
            for m in goal["milestones"]
            if m["is_included"] and m["id"] not in done
        ]
        title = goal["title"] if goal["title"].endswith(CARRIED) else goal["title"] + CARRIED
        if not open_milestones or title.casefold() in next_titles or goal["title"].casefold() in next_titles:
            continue
        added = store.add(
            kind="create_goal",
            source="weekly",
            summary=f"Carry “{goal['title']}” over to next week ({len(open_milestones)} open milestone(s))",
            details=[("New goal", title), ("Week of", next_monday.isoformat())]
            + [("Milestone", text) for text in open_milestones],
            payload=_goal_payload(
                next_week,
                title,
                open_milestones,
                description=goal["description"],
                outcome=goal["expected_outcome"],
            ),
            dedupe_key=f"carry:{goal['id']}:{next_monday.isoformat()}",
        )
        created += added is not None

    # Short recap from this week's records (the only model call here).
    facts = [
        fact
        for fact in collect_facts(services.goals, services.analysis, services.progress, today)
        if (fact.kind in ("journal", "pending") and fact.day and monday <= fact.day <= friday)
        or (fact.kind in ("goal", "milestone") and fact.day == monday)
    ]
    recap = gateway.recap_week(
        {fact.id: fact.text for fact in facts[:80]},
        week=f"{monday.isoformat()} to {friday.isoformat()}",
    )
    if recap.points:
        added = store.add(
            kind="note",
            source="weekly",
            summary=f"Your week in review ({monday.isoformat()} to {friday.isoformat()})",
            details=[("•", point) for point in recap.points],
            dedupe_key=f"recap:{monday.isoformat()}",
        )
        created += added is not None

    return created


def run_planning(
    services: AgentServices,
    store: ProposalStore,
    gateway,
    suggestion_store,
    settings,
    today: date,
) -> int:
    """Last week's report, recommended goals and this week's Mon-Fri plan."""
    monday, _ = week_to_plan(today)
    last_monday = monday - timedelta(days=7)
    this_week = PeriodSpec(kind="weekly", start_date=monday, timezone=services.timezone)
    created = 0

    unreported = [
        period
        for period in find_unreported_periods(services.reports)
        if period["kind"] == "weekly" and period["start_date"] == last_monday.isoformat()
    ]
    for period_row in unreported:
        spec = completed_period_spec(period_row)
        evidence = gather_advice_evidence(services.analysis, spec)
        advice = request_recommendations(gateway, evidence, spec)

        added = store.add(
            kind="save_report",
            source="weekly",
            summary=f"Save last week's report ({spec.start_date} to {spec.end_date}) with AI advice",
            details=[("Period", f"{spec.start_date} to {spec.end_date}")]
            + [("Advice", item.text) for item in advice.next_period_suggestions[:3]],
            payload={
                "request_id": str(uuid4()),
                "period": period_row,
                "advisory": advice.model_dump(),
                "evidence_hash": evidence.evidence_hash,
                "provenance": model_provenance(settings),
            },
            dedupe_key=f"report:{period_row['id']}",
        )
        created += added is not None

        if monday > today:
            continue  # goals for next week are proposed on Monday
        for index, recommendation in enumerate(advice.recommended_goals[:3]):
            title = recommendation.text.strip().rstrip(".")[:120]
            milestones = gateway.suggest_milestones(period=this_week, title=title).milestones[:5]
            added = store.add(
                kind="create_goal",
                source="weekly",
                summary=f"New goal for this week: “{title}”",
                details=[("Week of", monday.isoformat())] + [("Milestone", text) for text in milestones],
                payload=_goal_payload(this_week, title, milestones),
                dedupe_key=f"recgoal:{period_row['id']}:{index}",
            )
            created += added is not None

    # The Monday-to-Friday plan, if none was made for this week yet.
    if suggestion_store.get(WEEK_PLAN, monday.isoformat()) is None:
        try:
            make_week_plan(
                services,
                gateway,
                suggestion_store,
                model=settings.llm_model,
                today=today,
            )
        except NothingToPlan:
            return created
        added = store.add(
            kind="note",
            source="weekly",
            summary=f"Your Monday-to-Friday plan for the week of {monday.isoformat()} is ready",
            details=[("Open", "/week-plan")],
            dedupe_key=f"plan:{monday.isoformat()}",
        )
        created += added is not None

    return created
