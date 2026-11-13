import os
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    LongTable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)
import json


def _register_fonts() -> None:
    """Register TrueType fonts distributed with ReportLab."""
    registered = set(pdfmetrics.getRegisteredFontNames())

    if "GTVera" not in registered:
        pdfmetrics.registerFont(
            TTFont("GTVera", "Vera.ttf")
        )

    if "GTVeraBold" not in registered:
        pdfmetrics.registerFont(
            TTFont("GTVeraBold", "VeraBd.ttf")
        )


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "body": ParagraphStyle(
            "ReportBody",
            fontName="GTVera",
            fontSize=9,
            leading=13,
            spaceAfter=5,
            splitLongWords=True,
        ),
        "title": ParagraphStyle(
            "ReportTitle",
            fontName="GTVeraBold",
            fontSize=19,
            leading=24,
            spaceAfter=12,
        ),
        "heading": ParagraphStyle(
            "ReportHeading",
            fontName="GTVeraBold",
            fontSize=12,
            leading=16,
            spaceBefore=12,
            spaceAfter=7,
            keepWithNext=True,
        ),
        "subheading": ParagraphStyle(
            "ReportSubheading",
            fontName="GTVeraBold",
            fontSize=10,
            leading=14,
            spaceBefore=8,
            spaceAfter=5,
            keepWithNext=True,
        ),
    }


def _paragraph(value: object, style: ParagraphStyle) -> Paragraph:
    """Escape stored text before passing it to Paragraph markup."""
    text = escape(str(value))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return Paragraph(text.replace("\n", "<br/>"), style)


def _percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def _page_footer(canvas, document) -> None:
    canvas.saveState()
    canvas.setFont("GTVera", 8)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.drawString(
        document.leftMargin,
        12 * mm,
        "Goal Tracker",
    )
    canvas.drawRightString(
        A4[0] - document.rightMargin,
        12 * mm,
        f"Page {document.page}",
    )
    canvas.restoreState()


def _build_story(report: dict, width: float) -> list:
    facts = report["facts"]
    period = facts["period"]
    styles = _styles()
    story = []

    def text(value: object) -> None:
        story.append(_paragraph(value, styles["body"]))

    def heading(value: str) -> None:
        story.append(_paragraph(value, styles["heading"]))

    def items(title: str, records: list[dict]) -> None:
        heading(title)

        if not records:
            text("None recorded.")
            return

        for record in records:
            details = []
            state = record.get("state")
            minutes = record.get("minutes")

            if state and state != "not_applicable":
                details.append(state.replace("_", " "))

            if minutes is not None:
                details.append(f"{minutes} minutes")

            suffix = (
                f" ({'; '.join(details)})"
                if details
                else ""
            )

            text(f"- {record['description']}{suffix}")

    story.append(
        _paragraph(
            f"{period['kind'].title()} Progress Report",
            styles["title"],
        )
    )
    text(f"Period: {period['start_date']} to {period['end_date']}")
    text(f"Timezone: {period['timezone']}")
    text(f"Revision: {report['revision']}")
    text(f"Report ID: {report['id']}")
    text(f"Cutoff: {report['cutoff_date']}")
    text(f"Generated: {report['generated_at']}")

    heading("Overall completion")

    results = [
        ["Metric", "Result"],
        [
            "Original-plan completion",
            _percentage(facts["original_completion"]),
        ],
        [
            "Revised-plan completion",
            _percentage(facts["revised_completion"]),
        ],
        ["Original goals", str(facts["original_goal_count"])],
        ["Revised scored goals", str(facts["revised_goal_count"])],
        ["Cancelled goals", str(facts["cancelled_goal_count"])],
    ]

    table = LongTable(
        [
            [
                _paragraph(cell, styles["body"])
                for cell in row
            ]
            for row in results
        ],
        colWidths=[width * 0.68, width * 0.32],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#E8EEF5"),
                ),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.HexColor("#CCCCCC"),
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 5 * mm))
    text("Overall completion gives each scored goal equal weight.")

    heading("Data coverage and recorded time")
    text(
        f"Days with entries: {facts['recorded_days']} "
        f"of {facts['elapsed_days']}"
    )
    text(
        "Logging consistency: "
        f"{_percentage(facts['logging_consistency'])}"
    )
    text(
        "Days without entries represent missing information, "
        "not proof of inactivity."
    )

    minutes = facts["recorded_minutes"]
    text(
        "Recorded time: "
        + (
            f"{minutes} minutes"
            if minutes is not None
            else "Not provided"
        )
    )
    text(
        f"Recorded items without time: "
        f"{facts['items_without_time']}"
    )

    heading("Planned versus actual")
    text(f"Milestones due by cutoff: {facts['planned_due']}")
    text(
        "Those milestones completed by cutoff: "
        f"{facts['completed_due']}"
    )

    heading("Goals and milestone results")

    if not facts["goals"]:
        text("No goals were effective during this period.")

    for number, goal in enumerate(facts["goals"], start=1):
        story.append(
            _paragraph(
                f"{number}. {goal['title']}",
                styles["subheading"],
            )
        )

        if goal["is_original_plan"]:
            text(f"Original title: {goal['original_title']}")
            text(
                "Original expected outcome: "
                f"{goal['original_expected_outcome'] or 'Not provided'}"
            )
        else:
            text("Added after initial planning.")

        text(
            "Current expected outcome: "
            f"{goal['expected_outcome'] or 'Not provided'}"
        )

        if goal.get("description"):
            text(f"Description: {goal['description']}")

        text(f"Lifecycle: {goal['lifecycle_status']}")
        text(f"Deadline: {goal['deadline']}")
        text(
            "Original completion: "
            f"{_percentage(goal['original_completion'])}"
        )
        text(
            "Revised completion: "
            f"{_percentage(goal['revised_completion'])}"
        )

        if goal["overdue"]:
            text("Incomplete after a deadline.")

        for milestone in goal["milestones"]:
            status = (
                "Done" if milestone["completed"] else "Not done"
            )
            labels = []

            if not milestone["is_baseline"]:
                labels.append("added")

            if not milestone["is_included"]:
                labels.append("removed from revised plan")

            if milestone["overdue"]:
                labels.append("overdue")

            suffix = (
                f" ({'; '.join(labels)})"
                if labels
                else ""
            )

            text(
                f"- {status}: {milestone['description']}{suffix}"
            )

            if (
                milestone["is_baseline"]
                and milestone["creation_description"]
                != milestone["description"]
            ):
                text(
                    "Original milestone description: "
                    f"{milestone['creation_description']}"
                )

        text(
            f"Scope changes: {goal['added_milestones']} added, "
            f"{goal['removed_milestones']} removed."
        )

    items(
        "Completed activities recorded during the period",
        facts["completed_activities"],
    )

    completed_milestones = [
        {
            "description": (
                f"{goal['title']}: {milestone['description']}"
            )
        }
        for goal in facts["goals"]
        for milestone in goal["milestones"]
        if milestone["completed"] and milestone["is_included"]
    ]

    items(
        "Completed milestones as of cutoff",
        completed_milestones,
    )
    items("User-recorded achievements", facts["achievements"])
    items("Important learnings", facts["learnings"])
    items("Challenges encountered", facts["challenges"])
    items("Additional observations", facts["observations"])
    items(
        "Open work across tracked goals as of cutoff",
        facts["pending_activities"],
    )
    text(
        "Open work may include carryover from earlier periods "
        "and goals belonging to other periods."
    )

    heading("Daily completion trend")

    for day in facts["completion_trend"]:
        text(
            f"{day['date']}: "
            f"{_percentage(day['completion'])}; "
            f"{day['entries']} entries"
        )

        heading("AI recommendations")
    advisory = report["advisory"]

    if advisory is None:
        text("AI recommendations are not included in this revision.")

    else:
        provenance = report.get("model_provenance") or {}

        if provenance.get("model"):
            text(f"Model: {provenance['model']}")

        text(
            "These are suggestions. Recommended goals have not "
            "been created automatically."
        )

        sections = (
            ("Areas for improvement", "areas_for_improvement"),
            ("Next-period suggestions", "next_period_suggestions"),
            ("Recommended future goals", "recommended_goals"),
        )
        evidence = advisory["evidence"]

        for title, key in sections:
            story.append(
                _paragraph(title, styles["subheading"])
            )

            recommendations = advisory[key]

            if not recommendations:
                text("No suggestions.")
                continue

            for recommendation in recommendations:
                text(f"- {recommendation['text']}")

                for evidence_id in recommendation["evidence_ids"]:
                    record = json.loads(evidence[evidence_id])

                    description = (
                        record.get("description")
                        or record.get("title")
                        or (
                            f"{record.get('kind', 'Reporting')} period: "
                            f"{record.get('start_date', '')} to "
                            f"{record.get('end_date', '')}"
                        )
                    )

                    text(
                        f"Source [{evidence_id}]: {description}"
                    )

        omitted = advisory.get("omitted_count", 0)

        if omitted:
            text(
                f"Advice used a limited context: {omitted} records "
                "were omitted from the model input."
            )

    heading("Report history")
    text(
        "This document uses a saved factual snapshot. "
        "Later corrections appear in a newly generated report revision."
    )

    return story


def export_report_pdf(
    report: dict,
    output_directory: Path,
) -> Path:
    """Export a saved report without overwriting an existing PDF."""
    output_directory = output_directory.expanduser()

    if not output_directory.is_absolute():
        raise ValueError(
            "The report output directory must be absolute."
        )

    output_directory = output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    period = report["facts"]["period"]
    kind = period["kind"]

    if kind not in {"weekly", "monthly"}:
        raise ValueError("Invalid reporting period kind.")

    start = date.fromisoformat(period["start_date"])
    end = date.fromisoformat(period["end_date"])
    report_id = str(UUID(str(report["id"])))
    revision = int(report["revision"])

    if revision < 1:
        raise ValueError("The report revision must be positive.")

    filename = (
        f"{kind}_{start.isoformat()}_{end.isoformat()}_"
        f"r{revision}_{report_id}.pdf"
    )
    destination = output_directory / filename

    if destination.exists():
        raise FileExistsError(
            f"The PDF already exists: {destination}"
        )

    _register_fonts()

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".goal_tracker_",
        suffix=".pdf",
        dir=output_directory,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)

    try:
        document = SimpleDocTemplate(
            str(temporary_path),
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=22 * mm,
            title=f"{kind.title()} Goal Tracker Report",
            author="Goal Tracker",
        )

        document.build(
            _build_story(report, document.width),
            onFirstPage=_page_footer,
            onLaterPages=_page_footer,
        )

        # Publish the completed file without replacing an existing file.
        os.link(temporary_path, destination)

        return destination

    finally:
        temporary_path.unlink(missing_ok=True)