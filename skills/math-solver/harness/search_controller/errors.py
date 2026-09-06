"""Structured errors returned by the search controller."""

from typing import Any, Optional


class SearchError(Exception):
    """A stable, machine-readable controller error."""

    def __init__(
        self, code: str, message: str, details: Optional[Any] = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
