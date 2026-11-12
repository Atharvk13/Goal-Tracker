from enum import StrEnum


class PeriodKind(StrEnum):
    """Supported calendar periods."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"


class GoalLifecycle(StrEnum):
    """Whether a goal is available for active tracking."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class MilestoneState(StrEnum):
    """Explicit completion state of a milestone."""

    DONE = "done"
    NOT_DONE = "not_done"