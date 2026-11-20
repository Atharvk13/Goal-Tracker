"""Jarvis chat: answers from saved records and proposes confirmed actions.

The local model is the only brain. Answers must cite records; actions are
stored as proposals and run only when the user presses Confirm.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from goal_tracker.assist.actions import (
    ACTION_INTENTS,
    ACTIONABLE_KINDS,
    ActionRefused,
    build_proposal,
    looks_like_command,
    repair,
)
from goal_tracker.assist.chat import NO_RECORDS_ANSWER, collect_facts, select_facts
from goal_tracker.assist.proposals import ProposalStore
from goal_tracker.llm.lock import MODEL_LOCK
from goal_tracker.web.dependencies import (
    get_analysis_service,
    get_context,
    get_gateway,
    get_goal_service,
    get_progress_service,
    today,
)


router = APIRouter()

UNAVAILABLE = (
    "The local model is unavailable right now. Check that Ollama is "
    "running and try again."
)


class Turn(BaseModel):
    question: str = Field(max_length=1000)
    answer: str = Field(max_length=2000)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    history: list[Turn] = Field(default_factory=list, max_length=20)


def _reply(answered: bool, answer: str, sources=None, proposal=None, status=200):
    return JSONResponse(
        {
            "answered": answered,
            "answer": answer,
            "sources": sources or [],
            "proposal": proposal,
        },
        status_code=status,
    )


def proposal_view(proposal: dict) -> dict:
    """What the chat card shows for a proposal."""
    return {
        "id": proposal["id"],
        "summary": proposal["summary"],
        "details": proposal["details"],
        "status": proposal["status"],
        "result": proposal["result"],
    }


@router.post("/chat/ask", include_in_schema=False)
def ask(request: Request, body: ChatRequest):
    question = body.question.strip()
    if not question:
        return _reply(False, "Type a question about your goals or progress.", status=422)

    context = get_context(request)
    current = today(request)
    facts = collect_facts(
        get_goal_service(request),
        get_analysis_service(request),
        get_progress_service(request),
        current,
    )
    if not facts:
        return _reply(False, NO_RECORDS_ANSWER)

    chosen = select_facts(question, facts, current)
    records = {fact.id: fact.text for fact in chosen}
    gateway = get_gateway(request)

    # 1. A request for a change becomes a proposal the user confirms. Only
    #    records that can be acted on are sent, so journal history that
    #    mentions the same work cannot confuse the model.
    if looks_like_command(question):
        actionable = select_facts(
            question,
            [fact for fact in facts if fact.kind in ACTIONABLE_KINDS],
            current,
        )
        by_id = {fact.id: fact for fact in actionable}
        try:
            with MODEL_LOCK:
                decision = gateway.decide_action(
                    question,
                    {fact.id: fact.text for fact in actionable},
                    today=current.isoformat(),
                )
        except Exception as error:
            context.logger.warning("Jarvis action failed (%s).", type(error).__name__)
            return _reply(False, UNAVAILABLE, status=503)

        if decision.intent in ACTION_INTENTS:
            try:
                fields = build_proposal(repair(decision, by_id, question), by_id, current)
            except ActionRefused as refusal:
                return _reply(False, str(refusal))

            proposal = ProposalStore(context.paths.database).add(source="chat", **fields)
            return _reply(
                True,
                "Here's what I'll do. Nothing is saved until you confirm.",
                proposal=proposal_view(proposal),
            )

    # 2. Otherwise answer from the records only.
    try:
        with MODEL_LOCK:
            result = gateway.answer_from_records(
                question,
                records,
                today=current.isoformat(),
                history=[turn.model_dump() for turn in body.history],
            )
    except Exception as error:
        context.logger.warning("Chat answer failed (%s).", type(error).__name__)
        return _reply(False, UNAVAILABLE, status=503)

    if not result.answered:
        return _reply(False, NO_RECORDS_ANSWER)

    return _reply(
        True,
        result.answer.strip(),
        [{"id": source, "text": records[source]} for source in result.sources],
    )
