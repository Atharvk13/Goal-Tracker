"""Shared fixtures for web interface tests."""

from datetime import datetime
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import goal_tracker.web.app as web_module
import goal_tracker.web.dependencies as web_dependencies
from goal_tracker.bootstrap import AppContext
from goal_tracker.config.paths import get_app_paths
from goal_tracker.config.settings import Settings
from goal_tracker.db.migrate import migrate_database
from goal_tracker.goals.service import GoalService
from goal_tracker.progress.service import ProgressService

TIMEZONE = "Asia/Kolkata"


class _InputParser(HTMLParser):
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("name") == self.name:
            self.values.append(attributes.get("value"))


def input_values(html: str, name: str) -> list[str]:
    """Return the values of every <input> with this name."""
    parser = _InputParser(name)
    parser.feed(html)
    return parser.values


class FakeGateway:
    """Stands in for ModelGateway; set a result or an exception per method."""

    def __init__(self):
        self.suggestions = None
        self.proposal = None
        self.recommendations = None
        self.mail_draft = None
        self.goal_review = None
        self.week_plan = None
        self.chat_answer = None
        self.decision = None
        self.recap = None
        self.error: Exception | None = None
        self.calls: list[str] = []

    def _result(self, name, value):
        self.calls.append(name)
        if self.error is not None:
            raise self.error
        return value

    def suggest_milestones(self, **_):
        return self._result("suggest_milestones", self.suggestions)

    def extract_progress(self, _text):
        return self._result("extract_progress", self.proposal)

    def recommend_next_period(self, _evidence, *, period_kind=None):
        return self._result("recommend_next_period", self.recommendations)

    def write_daily_mail(self, answers):
        self.mail_answers = answers
        return self._result("write_daily_mail", self.mail_draft)

    def review_goal(self, goal):
        self.review_input = goal
        return self._result("review_goal", self.goal_review)

    def answer_from_records(self, question, records, *, today, history=None):
        self.chat_input = {"question": question, "records": records, "today": today, "history": history}
        return self._result("answer_from_records", self.chat_answer)

    def decide_action(self, message, records, *, today):
        self.action_input = {"message": message, "records": records, "today": today}
        return self._result("decide_action", self.decision)

    def recap_week(self, records, *, week):
        self.recap_input = {"records": records, "week": week}
        return self._result("recap_week", self.recap)

    def plan_week(self, week, days):
        self.week_input, self.week_days = week, days
        return self._result("plan_week", self.week_plan)


@pytest.fixture
def web(tmp_path, monkeypatch):
    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    context = AppContext(
        settings=Settings(llm_model="test-model", timezone=TIMEZONE, agent_schedule=False),
        paths=paths,
        logger=Mock(),
    )
    gateway = FakeGateway()

    monkeypatch.setattr(web_module, "bootstrap", Mock(return_value=context))
    monkeypatch.setattr(web_dependencies, "ModelGateway", lambda _settings: gateway)

    with TestClient(web_module.create_app()) as client:
        yield SimpleNamespace(
            client=client,
            context=context,
            paths=paths,
            gateway=gateway,
            today=datetime.now(ZoneInfo(TIMEZONE)).date(),
            goals=GoalService(paths.database, timezone=TIMEZONE),
            progress=ProgressService(paths.database, timezone=TIMEZONE),
        )
