import sys
from datetime import date
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from conftest import input_values

import goal_tracker.mail.outlook as outlook_module
from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.migrate import migrate_database
from goal_tracker.mail.log import MailLog
from goal_tracker.mail.outlook import (
    MailSendError,
    OutgoingMail,
    parse_addresses,
    send_with_outlook,
)

SUBJECT = "Daily Update – 19 November 2026"
BODY = "Hi Arpan,\r\n\r\n1. Meetings / Activities\r\n- Attended standup\r\n"


# ---------------------------------------------------------------- helpers


def test_parse_addresses():
    assert parse_addresses(" a@x.com; b@y.org ,c@z.io ") == [
        "a@x.com", "b@y.org", "c@z.io",
    ]
    assert parse_addresses("") == []

    with pytest.raises(ValueError, match="not-an-address"):
        parse_addresses("a@x.com; not-an-address")


def test_mail_log_reserves_each_request_once(tmp_path):
    paths = get_app_paths(tmp_path / "data")
    migrate_database(paths.database, paths.backups)
    log = MailLog(paths.database)
    request_id = str(uuid4())
    args = dict(
        request_id=request_id, mail_date=date(2026, 11, 19), to=["a@x.com"],
        cc=[], subject="S", body="B", method="outlook_send",
    )

    first, is_new = log.reserve(**args)
    again, is_new_again = log.reserve(**args)

    assert is_new and not is_new_again
    assert first["id"] == again["id"]
    assert first["status"] == "sending"

    done = log.complete(request_id)
    assert done["status"] == "sent" and done["completed_at"]
    assert [m["id"] for m in log.sent_on(date(2026, 11, 19))] == [first["id"]]


# ------------------------------------------------------- outlook automation


@pytest.fixture
def fake_com(monkeypatch):
    """Stand-in for pywin32 so no real Outlook is touched."""
    state = SimpleNamespace(item=None, calls=[])

    class ComError(Exception):
        pass

    class Item:
        def Send(self):
            state.calls.append("Send")

        def Display(self, modal):
            state.calls.append(("Display", modal))

    class Application:
        def CreateItem(self, kind):
            assert kind == 0
            state.item = Item()
            return state.item

    pythoncom = ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: state.calls.append("CoInitialize")
    pythoncom.CoUninitialize = lambda: state.calls.append("CoUninitialize")
    pywintypes = ModuleType("pywintypes")
    pywintypes.com_error = ComError
    client = ModuleType("win32com.client")
    client.Dispatch = lambda name: (
        Application() if name == "Outlook.Application" else None
    )
    win32com = ModuleType("win32com")
    win32com.client = client

    for name, module in {
        "pythoncom": pythoncom,
        "pywintypes": pywintypes,
        "win32com": win32com,
        "win32com.client": client,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    state.ComError = ComError
    state.client = client
    return state


def test_outlook_send_fills_and_sends_the_item(fake_com):
    send_with_outlook(OutgoingMail(["a@x.com", "b@y.com"], ["c@z.com"], "Subj", "Body"))

    item = fake_com.item
    assert (item.To, item.CC, item.Subject, item.Body) == (
        "a@x.com; b@y.com", "c@z.com", "Subj", "Body",
    )
    assert fake_com.calls == ["CoInitialize", "Send", "CoUninitialize"]


def test_outlook_draft_only_displays(fake_com):
    send_with_outlook(OutgoingMail(["a@x.com"], [], "S", "B"), draft_only=True)

    assert fake_com.calls == ["CoInitialize", ("Display", False), "CoUninitialize"]


def test_outlook_errors_become_mail_send_error(fake_com):
    def broken(name):
        raise fake_com.ComError("no profile")

    fake_com.client.Dispatch = broken

    with pytest.raises(MailSendError, match="classic Outlook"):
        send_with_outlook(OutgoingMail(["a@x.com"], [], "S", "B"))
    assert fake_com.calls[-1] == "CoUninitialize"


# ---------------------------------------------------------------- web flow


@pytest.fixture
def outbox(web, monkeypatch):
    sent = SimpleNamespace(mails=[], error=None)

    def fake_send(mail, *, draft_only=False):
        if sent.error:
            raise MailSendError(sent.error)
        sent.mails.append((mail, draft_only))

    monkeypatch.setattr(outlook_module, "send_with_outlook", fake_send)
    web.context.settings.mail_to = "arpan@example.com"
    web.context.settings.mail_cc = "lead@example.com"
    web.context.settings.mail_send_mode = "send"
    return sent


def _send(web, request_id=None, **data):
    form = {
        "on": web.today.isoformat(),
        "request_id": request_id or str(uuid4()),
        "subject": SUBJECT,
        "body": BODY,
        **data,
    }
    return web.client.post("/mail/send", data=form), form["request_id"]


def test_mail_pages_show_send_panel(web, outbox):
    page = web.client.get("/progress/mail")

    assert 'action="/mail/send"' in page.text
    assert "arpan@example.com" in page.text
    assert "Send mail now" in page.text
    assert input_values(page.text, "request_id")


def test_send_once_then_refuse_duplicate(web, outbox):
    response, request_id = _send(web)

    assert response.status_code == 200
    assert "Mail sent" in response.text
    mail, draft_only = outbox.mails[0]
    assert mail.to == ["arpan@example.com"] and mail.cc == ["lead@example.com"]
    assert mail.subject == SUBJECT
    assert mail.body == BODY.replace("\r\n", "\n")
    assert draft_only is False

    again, _ = _send(web, request_id=request_id)
    assert "Already handled" in again.text
    assert len(outbox.mails) == 1

    # The day's mail page now warns before a second send.
    assert "was already sent" in web.client.get("/progress/mail").text
    log = web.client.get("/mail/sent")
    assert SUBJECT in log.text and "Sent" in log.text


def test_failure_is_logged_and_offers_retry(web, outbox):
    outbox.error = "Outlook could not send the mail."

    response, request_id = _send(web)

    assert response.status_code == 502
    assert "Mail not sent" in response.text
    retry_id = input_values(response.text, "request_id")[0]
    assert retry_id != request_id
    assert "Attended standup" in response.text  # text kept for retry

    outbox.error = None
    retried, _ = _send(web, request_id=retry_id)
    assert "Mail sent" in retried.text
    assert len(outbox.mails) == 1


def test_draft_mode_opens_outlook(web, outbox):
    web.context.settings.mail_send_mode = "draft"

    response, _ = _send(web)

    assert "Opened in Outlook" in response.text
    assert outbox.mails[0][1] is True


def test_missing_recipient_blocks_sending(web, outbox):
    web.context.settings.mail_to = ""

    response, _ = _send(web)

    assert response.status_code == 400
    assert "GT_MAIL_TO" in response.text
    assert outbox.mails == []


def test_empty_subject_is_rejected(web, outbox):
    response, _ = _send(web, subject="  ")

    assert response.status_code == 422
    assert outbox.mails == []


# --------------------------------------------------------- open (mailto) mode


def test_open_mode_is_the_default(web):
    assert web.context.settings.mail_send_mode == "open"


def test_open_mode_shows_open_in_outlook_button(web, outbox):
    web.context.settings.mail_send_mode = "open"

    page = web.client.get("/progress/mail")

    assert "Open in Outlook" in page.text
    assert 'data-to="arpan@example.com"' in page.text
    assert 'data-cc="lead@example.com"' in page.text
    assert "Send mail now" not in page.text


def test_open_mode_never_sends_directly(web, outbox):
    web.context.settings.mail_send_mode = "open"

    response, _ = _send(web)

    assert response.status_code == 400
    assert "Direct sending is turned off" in response.text
    assert outbox.mails == []


def test_opening_is_logged_once_per_request(web, outbox):
    web.context.settings.mail_send_mode = "open"
    form = {
        "on": web.today.isoformat(),
        "request_id": str(uuid4()),
        "subject": SUBJECT,
        "body": BODY,
    }

    first = web.client.post("/mail/opened", data=form).json()
    second = web.client.post("/mail/opened", data=form).json()

    assert first["logged"] and first["new"]
    assert second["logged"] and not second["new"]
    assert first["id"] == second["id"]
    assert outbox.mails == []

    log = web.client.get("/mail/sent").text
    assert "Opened in Outlook" in log and SUBJECT in log
    assert "already opened in Outlook" in web.client.get("/progress/mail").text


def test_opening_without_recipient_is_not_logged(web, outbox):
    web.context.settings.mail_send_mode = "open"
    web.context.settings.mail_to = ""

    response = web.client.post(
        "/mail/opened",
        data={"on": web.today.isoformat(), "request_id": str(uuid4()),
              "subject": SUBJECT, "body": BODY},
    )

    assert response.status_code == 400
    assert response.json()["logged"] is False
