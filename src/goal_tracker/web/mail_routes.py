"""Daily status mail.

Two ways to produce it, both using the same template file:
- /mail: answer one question per section; the local model rewrites the
  answers into bullet points (your own words are used if it is unavailable).
- /progress/mail: filled automatically from progress already saved.
"""

from datetime import date

from fastapi import APIRouter, Request

from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.domain.periods import current_period_start
from goal_tracker.mail.compose import (
    MAIL_SECTIONS,
    answers_as_journal,
    merge_written_sections,
    section_placeholders,
)
from goal_tracker.mail.daily import (
    DEFAULT_TEMPLATE_PATH,
    PLACEHOLDERS,
    build_daily_context,
    load_template,
    render_daily_mail,
)
from goal_tracker.progress.workflow import load_all_pending, load_goal_lookup
from goal_tracker.web.dependencies import (
    get_analysis_service,
    get_context,
    get_gateway,
    get_goal_service,
    get_progress_service,
    templates,
    today,
)


router = APIRouter()

_PAGE_SIZE = 100


# ----------------------------------------------------------------- helpers


def _pick_day(request: Request, value: str | None, warnings: list[str]) -> date:
    current = today(request)

    try:
        day = date.fromisoformat(value) if value else current
    except ValueError:
        warnings.append("The date must use YYYY-MM-DD format; showing today.")
        return current

    if day > current:
        warnings.append("Mails can only be prepared for today or earlier; showing today.")
        return current

    return day


def _template(request: Request, warnings: list[str]):
    configured = get_context(request).settings.daily_mail_template

    try:
        return load_template(configured)
    except OSError:
        warnings.append(
            f"The template {configured} could not be read; "
            "using the bundled template instead."
        )
        return load_template(None)


def _entries_for(service, day: date) -> list[dict]:
    entries, offset = [], 0

    while True:
        page = service.get_daily_progress(day, day, limit=_PAGE_SIZE, offset=offset)
        entries.extend(page)

        if len(page) < _PAGE_SIZE:
            return entries

        offset += _PAGE_SIZE


def _saved_progress_context(request: Request, day: date) -> tuple[dict, int]:
    """Placeholder values from progress already saved for the day."""
    context = get_context(request)
    progress = get_progress_service(request)
    entries = _entries_for(progress, day)

    week_completion = None
    try:
        week = PeriodSpec(
            kind=PeriodKind.WEEKLY,
            start_date=current_period_start(PeriodKind.WEEKLY, day),
            timezone=context.settings.timezone,
        )
        week_completion = get_analysis_service(request).analyze_period(
            week, as_of=day
        )["revised_completion"]
    except AppError:
        pass

    values = build_daily_context(
        day,
        entries,
        load_goal_lookup(get_goal_service(request)),
        week_completion,
        load_all_pending(progress, day),
    )
    return values, len(entries)


def _unknown_warning(mail, warnings: list[str]) -> None:
    if mail.unknown_placeholders:
        warnings.append(
            "Unknown placeholders were left unchanged: "
            + ", ".join("{{" + name + "}}" for name in mail.unknown_placeholders)
        )


def _compose_form(
    request: Request,
    *,
    day: date,
    answers: dict[str, str],
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request=request,
        name="mail_compose.html",
        context={
            "day": day.isoformat(),
            "today": today(request).isoformat(),
            "sections": MAIL_SECTIONS,
            "answers": answers,
            "errors": errors or [],
            "warnings": warnings or [],
        },
        status_code=status_code,
    )


# ------------------------------------------------------------------ routes


@router.get("/mail", include_in_schema=False)
def compose_mail(request: Request, on: str | None = None):
    warnings: list[str] = []
    day = _pick_day(request, on, warnings)
    return _compose_form(request, day=day, answers={}, warnings=warnings)


@router.post("/mail", include_in_schema=False)
async def write_mail(request: Request):
    form = await request.form()
    warnings: list[str] = []
    day = _pick_day(request, form.get("on"), warnings)
    answers = {
        section.field: str(form.get(section.field, "")).replace("\r\n", "\n")
        for section in MAIL_SECTIONS
    }

    if form.get("action") == "edit":
        return _compose_form(request, day=day, answers=answers, warnings=warnings)

    if not any(value.strip() for value in answers.values()):
        return _compose_form(
            request,
            day=day,
            answers=answers,
            errors=["Answer at least one question to write the mail."],
            status_code=422,
        )

    written = None
    try:
        written = get_gateway(request).write_daily_mail(answers).model_dump()
    except Exception as error:
        get_context(request).logger.warning(
            "Daily mail writing failed (%s).",
            type(error).__name__,
        )
        warnings.append(
            "The local model is unavailable, so your answers were used as "
            "written. You can edit the text below before copying."
        )

    values, _ = _saved_progress_context(request, day)
    values.update(section_placeholders(merge_written_sections(answers, written)))

    template, _ = _template(request, warnings)
    mail = render_daily_mail(template, values)
    _unknown_warning(mail, warnings)

    return templates.TemplateResponse(
        request=request,
        name="mail_result.html",
        context={
            "day": day.isoformat(),
            "mail": mail,
            "answers": answers,
            "journal": answers_as_journal(answers),
            "ai_used": written is not None,
            "warnings": warnings,
        },
    )


@router.get("/progress/mail", include_in_schema=False)
def daily_mail(request: Request, on: str | None = None):
    warnings: list[str] = []
    day = _pick_day(request, on, warnings)
    template, template_path = _template(request, warnings)
    values, entry_count = _saved_progress_context(request, day)

    mail = render_daily_mail(template, values)
    _unknown_warning(mail, warnings)

    return templates.TemplateResponse(
        request=request,
        name="daily_mail.html",
        context={
            "day": day.isoformat(),
            "today": today(request).isoformat(),
            "mail": mail,
            "entry_count": entry_count,
            "warnings": warnings,
            "placeholders": PLACEHOLDERS,
            "template_path": str(template_path),
            "is_default_template": template_path == DEFAULT_TEMPLATE_PATH,
        },
    )
