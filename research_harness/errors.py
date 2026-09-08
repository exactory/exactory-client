"""Typed failures for research services and their command-line callers."""

from typing import Optional


class ResearchError(Exception):
    """An actionable service error which never terminates the interpreter."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> dict:
        result = {"code": self.code, "message": self.message}
        if self.details is not None:
            result["details"] = self.details
        return result
