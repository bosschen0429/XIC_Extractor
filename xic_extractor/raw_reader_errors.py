"""Errors shared by Thermo file and source-binding adapters."""


class RawReaderError(Exception):
    """Raised when Thermo raw reader setup or file opening fails."""


class RawReaderOpenError(RawReaderError):
    """Raised after a vendor open attempt with explicit cleanup truth."""

    def __init__(self, message: str, *, cleanup_failed: bool) -> None:
        super().__init__(message)
        if not isinstance(cleanup_failed, bool):
            raise TypeError("cleanup_failed must be a bool")
        self.cleanup_failed = cleanup_failed
