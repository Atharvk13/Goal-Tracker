-- Jarvis agent: proposed changes waiting for the user's confirmation, and a
-- record of scheduled runs so each one happens once per slot.
CREATE TABLE agent_proposals (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'update_work_status', 'log_work', 'complete_milestone',
        'add_milestone', 'move_milestone', 'create_goal', 'save_report', 'note'
    )),
    source TEXT NOT NULL CHECK (source IN ('chat', 'followup', 'weekly')),
    summary TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '[]',
    payload TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'dismissed', 'failed')),
    result TEXT,
    dedupe_key TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    resolved_at TEXT
);

CREATE INDEX agent_proposals_by_status ON agent_proposals (status, created_at);

CREATE TABLE agent_runs (
    job TEXT NOT NULL,
    run_key TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    PRIMARY KEY (job, run_key)
);
