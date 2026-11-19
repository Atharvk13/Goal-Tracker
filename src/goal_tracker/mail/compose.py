"""Daily mail written from the user's own answers, one question per section.

Each section of the user's template has a question. The answers are rewritten
into bullet points (by the local model when available) and placed into the
template placeholder for that section. Nothing here talks to the model; the
web route passes in what the model returned so this logic stays testable.
"""

import re
from dataclasses import dataclass

from goal_tracker.mail.daily import NONE_TEXT


@dataclass(frozen=True)
class MailSection:
    field: str          # name used in the form and the model schema
    placeholder: str    # template placeholder this section fills
    heading: str
    question: str
    example: str


MAIL_SECTIONS: tuple[MailSection, ...] = (
    MailSection(
        "meetings_activities",
        "other_tasks",
        "Meetings / Activities",
        "Which meetings, discussions or team activities did you take part in?",
        "Sprint planning with the team; pair-programmed on the login bug",
    ),
    MailSection(
        "use_cases_completed",
        "goal_tasks",
        "Use Cases / Activities Completed",
        "Which use cases, features or tasks did you work on or complete?",
        "Finished the report PDF export; started the email template feature",
    ),
    MailSection(
        "learning",
        "learnings",
        "Learning",
        "What did you learn or explore today?",
        "How FastAPI dependencies work; tried LangChain structured output",
    ),
    MailSection(
        "plan_for_tomorrow",
        "all_open_tasks",
        "Plan for Tomorrow",
        "What do you plan to work on tomorrow?",
        "Finish email template; write tests",
    ),
    MailSection(
        "blockers",
        "challenges",
        "Blockers / Support Required",
        "Any blockers, or support you need?",
        "Waiting for VPN access",
    ),
)

_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")


def answer_bullets(text: str) -> list[str]:
    """Split a free-text answer into bullets (one per line or semicolon)."""
    parts = re.split(r"[\n;]+", text.replace("\r\n", "\n"))
    bullets = [_BULLET_PREFIX.sub("", part).strip() for part in parts]
    return [bullet for bullet in bullets if bullet]


def merge_written_sections(
    answers: dict[str, str],
    written: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """Choose the bullets for each section.

    Model output is used only for sections the user actually answered, so it
    cannot invent content for an empty section. Without model output (or if
    it returned nothing for an answered section) the user's own words are used.
    """
    result = {}

    for section in MAIL_SECTIONS:
        answer = answers.get(section.field, "")
        own = answer_bullets(answer)

        if not own:
            result[section.field] = []
            continue

        model = [
            line.strip()
            for line in (written or {}).get(section.field, [])
            if line and line.strip()
        ]
        result[section.field] = model or own

    return result


def section_placeholders(bullets: dict[str, list[str]]) -> dict[str, str]:
    """Map section bullets onto the template placeholders they fill."""
    return {
        section.placeholder: (
            "\n".join(f"- {line}" for line in bullets.get(section.field, []))
            or NONE_TEXT
        )
        for section in MAIL_SECTIONS
    }


def answers_as_journal(answers: dict[str, str]) -> str:
    """Combine the answers into journal text for saving as progress."""
    blocks = [
        f"{section.heading}:\n{answers[section.field].strip()}"
        for section in MAIL_SECTIONS
        if answers.get(section.field, "").strip()
    ]
    return "\n\n".join(blocks)
