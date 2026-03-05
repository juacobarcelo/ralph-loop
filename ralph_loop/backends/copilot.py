from __future__ import annotations

import os
import selectors
import shutil
import subprocess
import sys
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

        stream_enabled = os.environ.get("RALPH_COPILOT_STREAM_OUTPUT", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        if stream_enabled:
            return self._execute_streaming(wrapped, cwd=cwd)

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

    def _execute_streaming(self, command: list[str], cwd: str | None = None) -> ExecutionResult:
        start = time.monotonic()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=cwd,
        )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        selector = selectors.DefaultSelector()
        if process.stdout is not None:
            selector.register(process.stdout, selectors.EVENT_READ)
        if process.stderr is not None:
            selector.register(process.stderr, selectors.EVENT_READ)

        while selector.get_map():
            for key, _ in selector.select(timeout=0.1):
                stream = key.fileobj
                line = stream.readline()
                if line == "":
                    selector.unregister(stream)
                    continue
                if stream is process.stdout:
                    stdout_chunks.append(line)
                    print(line, end="", flush=True)
                else:
                    stderr_chunks.append(line)
                    print(line, end="", file=sys.stderr, flush=True)

            if process.poll() is not None and not selector.get_map():
                break

        return_code = process.wait()
        duration = time.monotonic() - start
        return ExecutionResult(
            exit_code=return_code,
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            duration_seconds=duration,
            timed_out=return_code == 124,
            output_streamed=True,
        )

    def is_available(self) -> bool:
        return shutil.which("copilot") is not None
