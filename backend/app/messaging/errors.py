from __future__ import annotations


class PublisherError(RuntimeError):
    """A transport failure with an explicit retry classification."""

    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool = True,
        message: str | None = None,
    ) -> None:
        super().__init__(message or error_code)
        self.error_code = error_code
        self.retryable = retryable
