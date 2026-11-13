import json
from dataclasses import dataclass


MAX_CONTEXT_CHARACTERS = 24000


@dataclass(frozen=True)
class EvidenceSelection:
    """Selected evidence plus the number of records left out."""

    evidence: dict[str, str]
    omitted_count: int


def _encode(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
    )


def build_recommendation_evidence(
    facts: dict,
) -> EvidenceSelection:
    """Build bounded model context from a factual report snapshot."""
    candidates: dict[str, str] = {}
    period = facts["period"]

    candidates[f"period:{period['id']}"] = _encode(
        {
            "type": "reporting_period",
            "kind": period["kind"],
            "start_date": period["start_date"],
            "end_date": period["end_date"],
            "timezone": period["timezone"],
            "logging_note": (
                "Missing entries indicate missing information, "
                "not proof of inactivity."
            ),
        }
    )

    # Prioritize explicit challenges for improvement suggestions.
    for challenge in facts["challenges"]:
        candidates[f"activity:{challenge['id']}"] = _encode(
            {
                "type": "recorded_challenge",
                "description": challenge["description"],
            }
        )

    for goal in facts["goals"]:
        candidates[f"goal:{goal['id']}"] = _encode(
            {
                "type": "goal",
                "title": goal["title"],
                "expected_outcome": goal["expected_outcome"],
                "lifecycle_status": goal["lifecycle_status"],
                "completion_status": goal["completion_status"],
                "overdue": goal["overdue"],
                "is_original_plan": goal["is_original_plan"],
            }
        )

        for milestone in goal["milestones"]:
            candidates[
                f"milestone:{milestone['id']}"
            ] = _encode(
                {
                    "type": "milestone",
                    "goal_id": goal["id"],
                    "description": milestone["description"],
                    "completed": milestone["completed"],
                    "included_in_revised_plan": (
                        milestone["is_included"]
                    ),
                    "overdue": milestone["overdue"],
                }
            )

    for activity in facts["pending_activities"]:
        candidates[f"pending:{activity['id']}"] = _encode(
            {
                "type": "open_work_as_of_cutoff",
                "description": activity["description"],
                "state": activity["state"],
                "carried_over": activity.get(
                    "carried_over",
                    False,
                ),
                "scope_note": (
                    "This item may belong to another reporting "
                    "period. It is open work across tracked goals."
                ),
            }
        )

    categories = (
        ("learnings", "recorded_learning"),
        ("achievements", "recorded_achievement"),
        ("completed_activities", "recorded_completed_activity"),
        ("observations", "recorded_observation"),
    )

    for category, record_type in categories:
        for activity in facts[category]:
            candidates[f"activity:{activity['id']}"] = _encode(
                {
                    "type": record_type,
                    "description": activity["description"],
                    "state": activity.get("state"),
                }
            )

    selected: dict[str, str] = {}
    omitted_count = 0

    for evidence_id, description in candidates.items():
        proposed = {
            **selected,
            evidence_id: description,
        }

        context = _encode({"evidence": proposed})

        if len(context) > MAX_CONTEXT_CHARACTERS:
            omitted_count += 1
            continue

        selected[evidence_id] = description

    return EvidenceSelection(
        evidence=selected,
        omitted_count=omitted_count,
    )