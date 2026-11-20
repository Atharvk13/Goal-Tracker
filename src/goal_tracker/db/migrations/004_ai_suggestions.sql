-- AI suggestions kept so they are generated once and shown again later.
-- kind 'goal_review' is keyed by goal ID; 'week_plan' by the week's Monday.
-- evidence_hash records the inputs, so the page can say when the goal or
-- week changed after the suggestions were made.
CREATE TABLE ai_suggestions (
    kind TEXT NOT NULL CHECK (kind IN ('goal_review', 'week_plan')),
    subject_key TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    PRIMARY KEY (kind, subject_key)
);
