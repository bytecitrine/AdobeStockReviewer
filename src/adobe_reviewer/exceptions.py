"""Custom exception hierarchy.

Every error raised deliberately by this application derives from
:class:`AdobeReviewerError`, so callers can catch one base class at the top level.
"""

from __future__ import annotations


class AdobeReviewerError(Exception):
    """Base class for all application errors."""


class ConfigError(AdobeReviewerError):
    """Settings are missing, unreadable, or invalid."""


class PromptError(AdobeReviewerError):
    """A prompt file is missing or cannot be rendered."""


class ImageAnalysisError(AdobeReviewerError):
    """An image could not be opened or measured (corrupt, unsupported, etc.)."""


class OpenRouterError(AdobeReviewerError):
    """The OpenRouter API call failed.

    Attributes:
        status_code: HTTP status code if one was received.
        retryable: Whether retrying the request could plausibly succeed.
        retry_after: Seconds the server asked us to wait before retrying, if given.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


class ReviewParseError(AdobeReviewerError):
    """The model's reply was not valid JSON matching the review schema."""

    def __init__(self, message: str, *, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class ReportWriteError(AdobeReviewerError):
    """A review report could not be written to disk."""


class FileMoveError(AdobeReviewerError):
    """An image could not be moved to its destination folder."""
