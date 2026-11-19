"""Completed-period reports: generate (optionally with AI advice), view, PDF."""

import json
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError

from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import AppError, NotFoundError
from goal_tracker.llm.recommendations import ReportRecommendations
from goal_tracker.reporting.advice import (
    ADVICE_SECTIONS,
    completed_period_spec,
    gather_advice_evidence,
    model_provenance,
    recommendation_sections,
    request_recommendations,
)
from goal_tracker.reporting.periods import find_completed_period
from goal_tracker.web.dependencies import (
    get_analysis_service,
    get_context,
    get_gateway,
    get_report_service,
    templates,
)


router = APIRouter()

PAGE_SIZE = 20


def _evidence_label(raw: str | None) -> str:
    """Describe an evidence record the same way the terminal report does."""
    if raw is None:
        return "Evidence unavailable"

    record = json.loads(raw)
    return (
        record.get("description")
        or record.get("title")
        or (
            f"{record.get('kind', 'Reporting')} period: "
            f"{record.get('start_date', '')} to {record.get('end_date', '')}"
        )
    )


def _advice_view(sections, evidence: dict[str, str]) -> list[dict]:
    """Prepare advice sections with readable evidence for templates."""
    return [
        {
            "title": title,
            "items": [
                {
                    "text": item["text"],
                    "sources": [
                        (evidence_id, _evidence_label(evidence.get(evidence_id)))
                        for evidence_id in item["evidence_ids"]
                    ],
                }
                for item in items
            ],
        }
        for title, items in sections
    ]


def _require_period(request: Request, period_id: UUID) -> dict:
    period = find_completed_period(get_report_service(request), str(period_id))

    if period is None:
        raise HTTPException(
            status_code=404,
            detail="This period does not exist or has not ended yet.",
        )

    return period


def _period_response(
    request: Request,
    period: dict,
    *,
    error: str | None = None,
    warning: str | None = None,
    status_code: int = 200,
):
    reports = get_report_service(request).list_reports(
        period_id=period["id"],
        limit=PAGE_SIZE,
    )

    return templates.TemplateResponse(
        request=request,
        name="report_period.html",
        context={
            "period": period,
            "reports": reports,
            "request_id": str(uuid4()),
            "error": error,
            "warning": warning,
        },
        status_code=status_code,
    )


@router.get("/reports", include_in_schema=False)
def reports_home(
    request: Request,
    kind: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
    saved_page: Annotated[int, Query(ge=1)] = 1,
):
    service = get_report_service(request)
    kind_filter = PeriodKind(kind) if kind in {"weekly", "monthly"} else None

    periods = service.list_completed_periods(
        kind=kind_filter,
        limit=PAGE_SIZE + 1,
        offset=(page - 1) * PAGE_SIZE,
    )
    rows = []

    for period in periods[:PAGE_SIZE]:
        latest = service.list_reports(period_id=period["id"], limit=1)
        rows.append({"period": period, "latest": latest[0] if latest else None})

    saved = service.list_reports(
        limit=PAGE_SIZE + 1,
        offset=(saved_page - 1) * PAGE_SIZE,
    )

    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={
            "rows": rows,
            "kind": kind_filter.value if kind_filter else "",
            "page": page,
            "has_next": len(periods) > PAGE_SIZE,
            "saved": saved[:PAGE_SIZE],
            "saved_page": saved_page,
            "saved_has_next": len(saved) > PAGE_SIZE,
        },
    )


@router.get("/reports/periods/{period_id}", include_in_schema=False)
def report_period(request: Request, period_id: UUID):
    return _period_response(request, _require_period(request, period_id))


@router.post("/reports/periods/{period_id}/generate", include_in_schema=False)
def generate_report(
    request: Request,
    period_id: UUID,
    request_id: Annotated[UUID, Form()],
    include_ai: Annotated[str, Form()] = "",
):
    period_row = _require_period(request, period_id)
    period = completed_period_spec(period_row)

    if include_ai != "on":
        return _save(request, period_row, request_id=request_id)

    context = get_context(request)

    try:
        evidence = gather_advice_evidence(get_analysis_service(request), period)
        proposed = request_recommendations(get_gateway(request), evidence, period)
    except AppError as error:
        return _period_response(request, period_row, error=str(error), status_code=400)
    except Exception as error:
        context.logger.warning(
            "Report recommendations failed (%s).",
            type(error).__name__,
        )
        return _period_response(
            request,
            period_row,
            warning=(
                "AI suggestions are unavailable. "
                "You can still save a factual report."
            ),
            status_code=503,
        )

    sections = [
        (title, [item.model_dump() for item in items])
        for title, items in recommendation_sections(proposed)
    ]

    return templates.TemplateResponse(
        request=request,
        name="report_advice.html",
        context={
            "period": period_row,
            "sections": _advice_view(sections, evidence.selection.evidence),
            "evidence_count": len(evidence.selection.evidence),
            "omitted_count": evidence.selection.omitted_count,
            "advisory_json": proposed.model_dump_json(),
            "evidence_hash": evidence.evidence_hash,
            "provenance_json": json.dumps(model_provenance(context.settings)),
            "advice_request_id": str(uuid4()),
            "factual_request_id": str(uuid4()),
        },
    )


@router.post("/reports/periods/{period_id}/save", include_in_schema=False)
def save_report(
    request: Request,
    period_id: UUID,
    request_id: Annotated[UUID, Form()],
    advisory_json: Annotated[str, Form()] = "",
    evidence_hash: Annotated[str, Form()] = "",
    provenance_json: Annotated[str, Form()] = "",
):
    period_row = _require_period(request, period_id)

    advisory = None
    provenance = None

    if advisory_json:
        try:
            advisory = ReportRecommendations.model_validate_json(advisory_json)
            provenance = json.loads(provenance_json) if provenance_json else None
        except (ValidationError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="The AI suggestions are invalid. Generate them again.",
            ) from error

    return _save(
        request,
        period_row,
        request_id=request_id,
        advisory=advisory,
        provenance=provenance,
        evidence_hash=evidence_hash or None,
    )


def _save(
    request: Request,
    period_row: dict,
    *,
    request_id: UUID,
    advisory: ReportRecommendations | None = None,
    provenance: dict | None = None,
    evidence_hash: str | None = None,
):
    try:
        result = get_report_service(request).generate_report(
            completed_period_spec(period_row),
            request_id=request_id,
            advisory=advisory,
            model_provenance=provenance,
            expected_evidence_hash=evidence_hash,
        )
    except AppError as error:
        return _period_response(request, period_row, error=str(error), status_code=409)

    return RedirectResponse(
        url=f"/reports/{result['report']['id']}?saved=1",
        status_code=303,
    )


def _load_report(request: Request, report_id: UUID) -> dict:
    try:
        return get_report_service(request).get_report(report_id)
    except NotFoundError as error:
        raise HTTPException(
            status_code=404,
            detail="The requested report does not exist.",
        ) from error


@router.get("/reports/{report_id}", include_in_schema=False)
def report_details(request: Request, report_id: UUID, saved: str | None = None):
    report = _load_report(request, report_id)
    facts = report["facts"]
    advisory = report.get("advisory")

    advice = None
    if advisory is not None:
        advice = _advice_view(
            [(title, advisory[key]) for title, key in ADVICE_SECTIONS],
            advisory.get("evidence", {}),
        )

    completed_milestones = [
        f"{goal['title']}: {milestone['description']}"
        for goal in facts["goals"]
        for milestone in goal["milestones"]
        if milestone["completed"] and milestone["is_included"]
    ]

    return templates.TemplateResponse(
        request=request,
        name="report.html",
        context={
            "report": report,
            "facts": facts,
            "advice": advice,
            "completed_milestones": completed_milestones,
            "saved": saved == "1",
        },
    )


@router.get("/reports/{report_id}/pdf", include_in_schema=False)
def report_pdf(request: Request, report_id: UUID):
    _load_report(request, report_id)

    try:
        path = get_report_service(request).export_pdf(
            report_id,
            get_context(request).paths.reports,
        )
    except AppError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error

    return FileResponse(path, media_type="application/pdf", filename=path.name)
