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


@pytest.fixture
def web(tmp_path, monkeypatch):
    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    context = AppContext(
        settings=Settings(llm_model="test-model", timezone=TIMEZONE),
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
