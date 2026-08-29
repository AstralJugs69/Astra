"""Cross-layer domain failures with explicit recovery semantics."""


class BaselineStateConflictError(RuntimeError):
    """The requested baseline transition lost an optimistic-version race."""


class IncidentReviewStateConflictError(RuntimeError):
    """A professional review record lost an optimistic-version race."""


class IncidentReviewPrerequisiteError(RuntimeError):
    """A human action was attempted before its report/state prerequisite existed."""
