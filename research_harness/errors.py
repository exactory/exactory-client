"""Typed failures for research services and their command-line callers."""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
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


@contextmanager
def describe_error_at(path, **context):
    """Add absent output location and context to the same error from an existing validator."""
    try:
        yield
    except ResearchError as error:
        error.details = dict({'path': path, **context}, **(error.details or {}))
        raise


@contextmanager
def describe_tool_timeout():
    """At a command boundary, report a local tool that did not finish within its own time limit as a failed run.

    Below the boundary subprocess.TimeoutExpired is not a ResearchError, so it passes every validator unchanged:
    an unfinished tool never refuses, downgrades or caches anything about a record."""
    try:
        yield
    except subprocess.TimeoutExpired as error:
        arguments = error.cmd if isinstance(error.cmd, (list, tuple)) else str(error.cmd).split()
        tool = Path(os.fsdecode(arguments[0])).name
        raise ResearchError(
            'local_tool_timeout',
            f'The local tool {tool} did not finish within its {error.timeout:g}-second limit, so this run decided '
            'nothing about the record. When the machine has less load, run the command again.',
            {'tool': tool, 'budget_seconds': error.timeout}) from error
