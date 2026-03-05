from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExecutionResult:
    """Result of an AI CLI execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
    output_streamed: bool = False


@runtime_checkable
class Backend(Protocol):
    """Protocol implemented by all AI CLI backends."""

    @property
    def name(self) -> str:
        """Backend identifier."""

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 600,
        extra_flags: list[str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a prompt through backend CLI."""

    def is_available(self) -> bool:
        """Return whether backend CLI is available in PATH."""
