"""Typed failures for research services and their command-line callers."""

from typing import Optional


# A moderator removed the problem link, so attachment cannot succeed again and changes nothing.
# Other failures keep exit 1 (general) or 2 (usage and configuration).
REMOVED_PROBLEM_LINK_EXIT_CODE = 3


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
