class AppError(Exception):
    """Base class for errors that application interfaces can explain."""

    code = "APPLICATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.request_id = request_id


class DomainValidationError(AppError):
    """Input is structurally valid but violates an application rule."""

    code = "VALIDATION_ERROR"


class NotFoundError(AppError):
    """A requested record does not exist."""

    code = "NOT_FOUND"


class ConflictError(AppError):
    """An operation conflicts with existing data or a newer revision."""

    code = "CONFLICT"


class DuplicateReviewRequiredError(AppError):
    """A possible duplicate needs explicit user review."""

    code = "DUPLICATE_REVIEW_REQUIRED"


class StorageError(AppError):
    """A database operation could not be completed safely."""

    code = "STORAGE_ERROR"