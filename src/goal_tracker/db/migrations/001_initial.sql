-- Calendar weeks and months.
CREATE TABLE periods (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('weekly', 'monthly')),
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    planning_closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    CHECK (start_date <= end_date),
    UNIQUE (kind, start_date, end_date, timezone)
);

-- Shared idempotency records for confirmed mutations.
CREATE TABLE mutation_requests (
    request_id TEXT PRIMARY KEY NOT NULL,
    operation TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

-- Stable goal identities.
CREATE TABLE goals (
    id TEXT PRIMARY KEY NOT NULL,
    period_id TEXT NOT NULL REFERENCES periods(id),
    predecessor_goal_id TEXT REFERENCES goals(id),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1),
    is_original_plan INTEGER NOT NULL CHECK (is_original_plan IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

-- Goal descriptions and lifecycle changes are versioned.
CREATE TABLE goal_revisions (
    goal_id TEXT NOT NULL REFERENCES goals(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    title TEXT NOT NULL CHECK (length(trim(title)) > 0),
    description TEXT NOT NULL DEFAULT '',
    expected_outcome TEXT NOT NULL DEFAULT '',
    target_date TEXT,
    lifecycle_status TEXT NOT NULL CHECK (
        lifecycle_status IN ('active', 'cancelled', 'archived')
    ),
    effective_date TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    PRIMARY KEY (goal_id, revision)
);

-- Stable milestone identities; baseline membership never changes.
CREATE TABLE milestones (
    id TEXT PRIMARY KEY NOT NULL,
    goal_id TEXT NOT NULL REFERENCES goals(id),
    is_baseline INTEGER NOT NULL CHECK (is_baseline IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

-- Editing or removing a milestone creates a revision.
CREATE TABLE milestone_revisions (
    milestone_id TEXT NOT NULL REFERENCES milestones(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    due_date TEXT,
    is_included INTEGER NOT NULL CHECK (is_included IN (0, 1)),
    effective_date TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    PRIMARY KEY (milestone_id, revision)
);

-- Original journal input is retained.
CREATE TABLE entries (
    id TEXT PRIMARY KEY NOT NULL,
    activity_date TEXT NOT NULL,
    raw_text TEXT NOT NULL CHECK (length(trim(raw_text)) > 0),
    source TEXT NOT NULL CHECK (
        source IN ('manual', 'llm_reviewed')
    ),
    normalized_text_hash TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES mutation_requests(request_id),
    supersedes_entry_id TEXT UNIQUE REFERENCES entries(id),
    created_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    CHECK (supersedes_entry_id IS NULL OR supersedes_entry_id != id)
);

-- Structured items extracted or manually recorded from entries.
CREATE TABLE activities (
    id TEXT PRIMARY KEY NOT NULL,
    entry_id TEXT NOT NULL REFERENCES entries(id),
    kind TEXT NOT NULL CHECK (
        kind IN (
            'activity',
            'learning',
            'challenge',
            'observation',
            'achievement'
        )
    ),
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    initial_state TEXT NOT NULL CHECK (
        initial_state IN (
            'completed',
            'pending',
            'in_progress',
            'not_applicable'
        )
    ),
    minutes INTEGER CHECK (minutes IS NULL OR minutes >= 0),
    evidence_quote TEXT,
    updates_activity_id TEXT REFERENCES activities(id),
    CHECK (updates_activity_id IS NULL OR updates_activity_id != id)
);

-- One activity can support several goals.
CREATE TABLE activity_goal_links (
    activity_id TEXT NOT NULL REFERENCES activities(id),
    goal_id TEXT NOT NULL REFERENCES goals(id),
    PRIMARY KEY (activity_id, goal_id)
);

-- Pending and ongoing activities retain state-change history.
CREATE TABLE activity_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    activity_id TEXT NOT NULL REFERENCES activities(id),
    state TEXT NOT NULL CHECK (
        state IN ('completed', 'pending', 'in_progress', 'not_applicable')
    ),
    effective_date TEXT NOT NULL,
    source_entry_id TEXT NOT NULL REFERENCES entries(id),
    supersedes_event_id TEXT UNIQUE REFERENCES activity_events(id),
    recorded_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    CHECK (supersedes_event_id IS NULL OR supersedes_event_id != id)
);

-- Completion changes are explicit and separate from journal prose.
CREATE TABLE milestone_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    milestone_id TEXT NOT NULL REFERENCES milestones(id),
    state TEXT NOT NULL CHECK (state IN ('done', 'not_done')),
    effective_date TEXT NOT NULL,
    source_entry_id TEXT NOT NULL REFERENCES entries(id),
    source_activity_id TEXT REFERENCES activities(id),
    supersedes_event_id TEXT UNIQUE REFERENCES milestone_events(id),
    recorded_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    CHECK (supersedes_event_id IS NULL OR supersedes_event_id != id)
);

-- Trace confirmed changes without rewriting historical records.
CREATE TABLE audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    reason TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES mutation_requests(request_id),
    recorded_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    )
);

-- Saved reports are immutable snapshots with numbered revisions.
CREATE TABLE reports (
    id TEXT PRIMARY KEY NOT NULL,
    period_id TEXT NOT NULL REFERENCES periods(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    cutoff_date TEXT NOT NULL,
    factual_snapshot_json TEXT NOT NULL,
    advisory_json TEXT,
    model_provenance_json TEXT,
    evidence_hash TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES mutation_requests(request_id),
    export_status TEXT NOT NULL DEFAULT 'not_exported' CHECK (
        export_status IN ('not_exported', 'exported', 'failed')
    ),
    export_path TEXT,
    generated_at TEXT NOT NULL DEFAULT (
        strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
    ),
    UNIQUE (period_id, revision)
);

-- Indexes support common retrieval and history queries.
CREATE INDEX idx_goals_period
    ON goals(period_id);

CREATE INDEX idx_goal_revisions_effective
    ON goal_revisions(goal_id, effective_date, revision);

CREATE INDEX idx_milestones_goal
    ON milestones(goal_id);

CREATE INDEX idx_milestone_revisions_effective
    ON milestone_revisions(milestone_id, effective_date, revision);

CREATE INDEX idx_entries_activity_date
    ON entries(activity_date);

-- Deliberately non-unique: repeated text requires review, not automatic rejection.
CREATE INDEX idx_entries_duplicate_candidates
    ON entries(activity_date, normalized_text_hash);

CREATE INDEX idx_activities_entry
    ON activities(entry_id);

CREATE INDEX idx_activities_updates
    ON activities(updates_activity_id);

CREATE INDEX idx_activity_goal_links_goal
    ON activity_goal_links(goal_id);

CREATE INDEX idx_activity_events_history
    ON activity_events(activity_id, effective_date, sequence);

CREATE INDEX idx_milestone_events_history
    ON milestone_events(milestone_id, effective_date, sequence);

CREATE INDEX idx_audit_events_entity
    ON audit_events(entity_type, entity_id, sequence);

CREATE INDEX idx_reports_period
    ON reports(period_id, revision);