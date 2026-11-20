"""Scheduled Jarvis runs while the web app is open.

Each job runs at most once per slot (recorded in agent_runs), catches up
if the app was opened late, and only prepares suggestions. Model calls share
MODEL_LOCK with chat, so the model is never loaded twice at once. A run that
fails (for example, Ollama is not running) is retried at the next tick.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from goal_tracker.assist.followups import run_followups
from goal_tracker.assist.proposals import ProposalStore, services_for
from goal_tracker.assist.store import SuggestionStore
from goal_tracker.assist.weekly import run_planning, run_wrapup
from goal_tracker.llm.gateway import ModelGateway
from goal_tracker.llm.lock import MODEL_LOCK


TICK_SECONDS = 600
FIRST_TICK_SECONDS = 90


def _week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass(frozen=True)
class Job:
    name: str
    title: str
    due: Callable[[datetime], bool]
    slot: Callable[[datetime], str]


JOBS = (
    Job(
        "followups",
        "Pending work & deadline follow-ups",
        # Weekdays from 18:00.
        lambda now: now.weekday() < 5 and now.hour >= 18,
        lambda now: now.date().isoformat(),
    ),
    Job(
        "wrapup",
        "Friday wrap-up",
        # Friday from 17:00, or over the weekend if the app was closed then.
        lambda now: (now.weekday() == 4 and now.hour >= 17) or now.weekday() >= 5,
        _week_key,
    ),
    Job(
        "planning",
        "Monday planning",
        # Monday from 09:00, or by Wednesday if the app was closed then.
        lambda now: (now.weekday() == 0 and now.hour >= 9) or 0 < now.weekday() < 3,
        _week_key,
    ),
)

JOB_NAMES = {job.name: job for job in JOBS}


def due_jobs(now: datetime, store: ProposalStore) -> list[Job]:
    return [job for job in JOBS if job.due(now) and not store.has_run(job.name, job.slot(now))]


def run_job(name: str, context, now: datetime | None = None, gateway=None) -> int:
    """Run one job now (also used by the "Run now" buttons)."""
    settings = context.settings
    now = now or datetime.now(ZoneInfo(settings.timezone))
    today = now.date()
    database = context.paths.database
    store = ProposalStore(database)
    services = services_for(context)

    if name == "followups":
        return run_followups(services, store, today)

    gateway = gateway or ModelGateway(settings)
    with MODEL_LOCK:
        if name == "wrapup":
            return run_wrapup(services, store, gateway, today)
        if name == "planning":
            return run_planning(services, store, gateway, SuggestionStore(database), settings, today)

    raise KeyError(name)


def tick(context, now: datetime | None = None, gateway=None) -> list[str]:
    """Run every due job once; returns the names that completed."""
    now = now or datetime.now(ZoneInfo(context.settings.timezone))
    store = ProposalStore(context.paths.database)
    finished = []

    for job in due_jobs(now, store):
        try:
            run_job(job.name, context, now, gateway)
        except Exception as error:  # retried at the next tick
            context.logger.warning("Jarvis %s run failed (%s).", job.name, type(error).__name__)
            continue
        store.mark_run(job.name, job.slot(now))
        finished.append(job.name)

    return finished


async def run_scheduler(context) -> None:
    """Background loop started with the web app."""
    await asyncio.sleep(FIRST_TICK_SECONDS)
    while True:
        try:
            await asyncio.to_thread(tick, context)
        except Exception as error:
            context.logger.warning("Jarvis scheduler tick failed (%s).", type(error).__name__)
        await asyncio.sleep(TICK_SECONDS)
