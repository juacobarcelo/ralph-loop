from __future__ import annotations

import shutil
import subprocess
import time

from ralph_loop.backends.base import ExecutionResult


class ClaudeBackend:
    """Backend implementation for Claude CLI."""

    name = "claude"

    def __init__(self, max_turns: int = 20, budget_usd: float | None = None) -> None:
        self.max_turns = max_turns
        self.budget_usd = budget_usd

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 600,
        extra_flags: list[str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        command = ["claude", "-p", "--dangerously-skip-permissions"]
        command.extend(["--max-turns", str(self.max_turns)])
        command.extend(["--output-format", "json"])
        if model:
            command.extend(["--model", model])
        if self.budget_usd is not None:
            command.extend(["--max-budget-usd", str(self.budget_usd)])
        if extra_flags:
            command.extend(extra_flags)
        command.append(prompt)
        wrapped = ["timeout", str(timeout_seconds)] + command

        start = time.monotonic()
        result = subprocess.run(wrapped, capture_output=True, text=True, cwd=cwd, check=False)
        duration = time.monotonic() - start
        return ExecutionResult(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=duration,
            timed_out=result.returncode == 124,
        )

    def is_available(self) -> bool:
        return shutil.which("claude") is not None
