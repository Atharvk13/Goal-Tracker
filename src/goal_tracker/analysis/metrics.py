from collections.abc import Iterable


def _validate_counts(completed: int, total: int) -> None:
    if total < 0 or completed < 0 or completed > total:
        raise ValueError(
            "Counts must be nonnegative, and completed cannot exceed total."
        )


def completion_percentage(
    completed: int,
    total: int,
) -> float | None:
    """Calculate completion, using None when there is nothing to score."""
    _validate_counts(completed, total)

    if total == 0:
        return None

    return round(100 * completed / total, 2)


def overall_completion(
    goal_counts: Iterable[tuple[int, int]],
) -> float | None:
    """Average goal percentages with equal weight for each scored goal."""
    percentages = []

    for completed, total in goal_counts:
        _validate_counts(completed, total)

        if total > 0:
            percentages.append(100 * completed / total)

    if not percentages:
        return None

    return round(sum(percentages) / len(percentages), 2)


def logging_consistency(
    recorded_days: int,
    elapsed_days: int,
) -> float | None:
    """Measure recorded days, without inferring unrecorded activity."""
    return completion_percentage(recorded_days, elapsed_days)