from __future__ import annotations

import shutil
import subprocess
import time

from ralph_loop.backends.base import ExecutionResult


class CopilotBackend:
    """Backend implementation for Copilot CLI."""

    name = "copilot"

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 600,
        extra_flags: list[str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        command = ["copilot", "--allow-all-tools", "--prompt", prompt]
        if model:
            command.extend(["--model", model])
        if extra_flags:
            command.extend(extra_flags)
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
        return shutil.which("copilot") is not None
