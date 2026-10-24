"""Error taxonomy shared by every module; adapters map `code` to exit/HTTP status."""

from __future__ import annotations


class TentaclesError(Exception):
    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ConfigError(TentaclesError):
    """Invalid configuration or arguments."""

    code = "invalid"


class NotFound(TentaclesError):
    code = "not_found"


class Conflict(TentaclesError):
    """Version conflict (e.g. applying a stale proposal)."""

    code = "conflict"


class Busy(TentaclesError):
    """The job is already running elsewhere."""

    code = "busy"


class UpstreamError(TentaclesError):
    """A model, mailbox or website failed."""

    code = "upstream"


EXIT_CODES = {"invalid": 2, "not_found": 3, "conflict": 4, "busy": 4, "upstream": 5, "aborted": 130}


def exit_code(code: str) -> int:
    return EXIT_CODES.get(code, 1)
