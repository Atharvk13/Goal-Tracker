"""Jarvis suggestions: confirm or dismiss proposals, run agent jobs now."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from goal_tracker.assist.proposals import ProposalStore, confirm, services_for
from goal_tracker.web.dependencies import get_context, get_gateway, templates, today
from goal_tracker.web.scheduler import JOB_NAMES, JOBS, run_job


router = APIRouter()

SOURCES = (
    ("chat", "From your chats"),
    ("followup", "Follow-ups"),
    ("weekly", "Weekly"),
)


def _store(request: Request) -> ProposalStore:
    return ProposalStore(get_context(request).paths.database)


def _wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def _outcome(request: Request, proposal: dict):
    if _wants_json(request):
        return JSONResponse(
            {
                "id": proposal["id"],
                "status": proposal["status"],
                "result": proposal["result"],
            }
        )
    return RedirectResponse(url="/agent", status_code=303)


@router.get("/agent", include_in_schema=False)
def agent_page(request: Request, ran: str = "", count: int = -1, error: str = ""):
    store = _store(request)
    pending = store.by_status("pending", limit=100)

    return templates.TemplateResponse(
        request=request,
        name="agent.html",
        context={
            "groups": [
                (title, [p for p in pending if p["source"] == source])
                for source, title in SOURCES
            ],
            "pending_count": len(pending),
            "recent": store.recent_resolved(limit=10),
            "jobs": JOBS,
            "ran": JOB_NAMES[ran].title if ran in JOB_NAMES else "",
            "ran_count": count,
            "error": error,
            "schedule_on": get_context(request).settings.agent_schedule,
        },
    )


@router.get("/agent/count", include_in_schema=False)
def agent_count(request: Request):
    return {"pending": _store(request).count_pending()}


@router.post("/agent/proposals/{proposal_id}/confirm", include_in_schema=False)
def confirm_proposal(request: Request, proposal_id: UUID):
    context = get_context(request)
    try:
        proposal = confirm(_store(request), str(proposal_id), services_for(context), today(request))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="This suggestion does not exist.") from error
    return _outcome(request, proposal)


@router.post("/agent/proposals/{proposal_id}/dismiss", include_in_schema=False)
def dismiss_proposal(request: Request, proposal_id: UUID):
    store = _store(request)
    if store.get(str(proposal_id)) is None:
        raise HTTPException(status_code=404, detail="This suggestion does not exist.")
    return _outcome(request, store.resolve(str(proposal_id), "dismissed"))


@router.post("/agent/run", include_in_schema=False)
def run_now(request: Request, job: Annotated[str, Form()]):
    if job not in JOB_NAMES:
        raise HTTPException(status_code=404, detail="Unknown Jarvis job.")

    context = get_context(request)
    try:
        created = run_job(job, context, gateway=get_gateway(request) if job != "followups" else None)
    except Exception as error:
        context.logger.warning("Jarvis %s run failed (%s).", job, type(error).__name__)
        return agent_page(
            request,
            error=(
                f"{JOB_NAMES[job].title} could not finish ({type(error).__name__}). "
                "Check that Ollama is running and try again."
            ),
        )

    return RedirectResponse(url=f"/agent?ran={job}&count={created}", status_code=303)
