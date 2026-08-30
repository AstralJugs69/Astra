"""Cross-layer domain failures with explicit recovery semantics."""


class BaselineStateConflictError(RuntimeError):
    """The requested baseline transition lost an optimistic-version race."""


class IncidentReviewStateConflictError(RuntimeError):
    """A professional review record lost an optimistic-version race."""


class IncidentReviewPrerequisiteError(RuntimeError):
    """A human action was attempted before its report/state prerequisite existed."""


class IncidentReviewEvidenceError(IncidentReviewPrerequisiteError):
    """A required immutable review-evidence fact is absent, stale, or mismatched."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
