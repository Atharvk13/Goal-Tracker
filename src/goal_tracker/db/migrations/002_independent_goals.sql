-- One shared "Independent tasks" goal per week collects work that is not
-- related to a planned goal. It is flagged so it never affects completion.
ALTER TABLE goals
    ADD COLUMN is_independent INTEGER NOT NULL DEFAULT 0
    CHECK (is_independent IN (0, 1));
