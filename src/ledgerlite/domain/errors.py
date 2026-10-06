"""Domain error hierarchy.

The API layer maps each class to an HTTP status code
(422, 404 and 409 respectively).
"""


class LedgerError(Exception):
    """Base class for every error raised by the ledger."""

    code = "ledger_error"


class ValidationError(LedgerError):
    """The input violates a structural rule (shape, range, balance...)."""

    code = "validation_error"


class NotFoundError(LedgerError):
    """A referenced entity does not exist."""

    code = "not_found"


class ConflictError(LedgerError):
    """The operation is well formed but conflicts with the current state."""

    code = "conflict"
