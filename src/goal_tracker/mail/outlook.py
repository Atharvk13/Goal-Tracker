"""Send mail through the classic Outlook desktop app on this computer.

Outlook sends with the account it is signed in to, so the application never
stores a mail password. The new Outlook for Windows cannot be automated; only
classic Outlook (Office 16) exposes the COM interface used here.
"""

import re
from dataclasses import dataclass

_ADDRESS = re.compile(r"^[^@\s;,]+@[^@\s;,]+\.[^@\s;,]+$")


class MailSendError(Exception):
    """Outlook could not create or send the message."""


@dataclass(frozen=True)
class OutgoingMail:
    to: list[str]
    cc: list[str]
    subject: str
    body: str


def parse_addresses(text: str | None) -> list[str]:
    """Split 'a@x.com; b@y.com' (or commas) into validated addresses."""
    addresses = [part.strip() for part in re.split(r"[;,]", text or "")]
    addresses = [address for address in addresses if address]

    invalid = [address for address in addresses if not _ADDRESS.match(address)]
    if invalid:
        raise ValueError(f"Invalid email address: {', '.join(invalid)}")

    return addresses


def send_with_outlook(mail: OutgoingMail, *, draft_only: bool = False) -> None:
    """Send the mail, or open it as a draft for the user to send."""
    try:
        import pythoncom
        import pywintypes
        import win32com.client
    except ImportError as error:
        raise MailSendError(
            "Outlook automation is only available on Windows with pywin32."
        ) from error

    # Web requests run in worker threads; each needs its own COM setup.
    pythoncom.CoInitialize()

    try:
        outlook = win32com.client.Dispatch("Outlook.Application")
        item = outlook.CreateItem(0)  # 0 = olMailItem
        item.To = "; ".join(mail.to)
        item.CC = "; ".join(mail.cc)
        item.Subject = mail.subject
        item.Body = mail.body

        if draft_only:
            item.Display(False)
        else:
            item.Send()

    except pywintypes.com_error as error:
        raise MailSendError(
            "Outlook could not send the mail. Make sure classic Outlook is "
            "installed and signed in to your work account."
        ) from error

    finally:
        pythoncom.CoUninitialize()
