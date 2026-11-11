"""Typed exceptions used across pyopenapi."""

from __future__ import annotations

from typing import Any


class PyOpenAPIError(Exception):
    """Base class for all pyopenapi errors.

    Every error carries a human-readable ``message`` and an optional actionable
    ``hint`` describing the next step. ``details`` holds extra structured context
    (offending value, path, etc.) that agents can use to self-correct.
    """

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"error": type(self).__name__, "message": self.message}
        if self.hint:
            data["hint"] = self.hint
        if self.details:
            data["details"] = self.details
        return data


class FrameworkDetectionError(PyOpenAPIError):
    """Raised when the web framework cannot be determined."""


class FrameworkNotSupportedError(PyOpenAPIError):
    """Raised when a detected framework is not supported (yet)."""


class AnalysisError(PyOpenAPIError):
    """Raised when project analysis fails."""


class AnalysisNotFoundError(PyOpenAPIError):
    """Raised when a cached analysis id does not exist."""


class CacheError(PyOpenAPIError):
    """Raised when cache maintenance (prune/stats) fails."""


class SpecGenerationError(PyOpenAPIError):
    """Raised when OpenAPI spec generation fails."""


class LLMNotConfiguredError(SpecGenerationError):
    """Raised when no LLM provider is configured for generation."""


class LLMBudgetExceededError(SpecGenerationError):
    """Raised when the configured LLM token budget is exhausted."""


class MarkdownGenerationError(PyOpenAPIError):
    """Raised when Markdown generation fails."""


class SpecValidationError(PyOpenAPIError):
    """Raised when a supplied OpenAPI document cannot be parsed."""


class WorkspaceError(PyOpenAPIError):
    """Raised when a workspace manifest cannot be processed."""


class AuthenticationError(PyOpenAPIError):
    """Raised when an HTTP request fails authentication."""


class RateLimitError(PyOpenAPIError):
    """Raised when a client exceeds the configured request rate."""


class RequestTooLargeError(PyOpenAPIError):
    """Raised when an HTTP request body exceeds the configured limit."""
