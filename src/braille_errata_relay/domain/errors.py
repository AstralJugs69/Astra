"""Cross-layer domain failures with explicit recovery semantics."""


class BaselineStateConflictError(RuntimeError):
    """The requested baseline transition lost an optimistic-version race."""
