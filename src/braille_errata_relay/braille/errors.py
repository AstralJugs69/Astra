"""Explicit fail-closed errors for the deterministic Braille boundary."""


class BraillePipelineError(ValueError):
    """Base error for invalid or unsupported deterministic input."""


class UnsupportedContentError(BraillePipelineError):
    """The source contains structure outside the supported fixture grammar."""


class ProfileNotReadyError(BraillePipelineError):
    """The installed Liblouis/toolchain does not match a bound profile."""


class IncompatibleBaselineError(BraillePipelineError):
    """A baseline was created with a renderer/profile that cannot be compared."""


class LiblouisUnavailableError(BraillePipelineError):
    """The pinned upstream Liblouis Python binding is unavailable."""


class TranslationError(BraillePipelineError):
    """Liblouis returned an invalid or incomplete result."""
