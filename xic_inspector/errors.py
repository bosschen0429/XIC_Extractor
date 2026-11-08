"""Inspector failures shared by transport and report generation."""


class InspectorError(Exception):
    """Base error for XIC Inspector with deterministic exit/failure code."""

    def __init__(self, code: int, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        if cause is not None:
            self.__cause__ = cause


class InspectorCancelled(InspectorError):
    """Raised when report generation is explicitly cancelled."""

    def __init__(self, message: str = "Report generation cancelled") -> None:
        super().__init__(code=130, message=message)
