-- Daily mails sent (or opened as drafts) through Outlook. A row is reserved
-- before sending so a repeated request ID can never send the mail twice.
CREATE TABLE sent_mails (
    id TEXT PRIMARY KEY NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    mail_date TEXT NOT NULL,
    recipients_to TEXT NOT NULL,
    recipients_cc TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    method TEXT NOT NULL CHECK (method IN ('outlook_send', 'outlook_draft')),
    status TEXT NOT NULL CHECK (status IN ('sending', 'sent', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    completed_at TEXT
);

CREATE INDEX sent_mails_by_date ON sent_mails (mail_date, created_at);
