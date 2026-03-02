from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from ralph_loop.backends.base import Backend, ExecutionResult
from ralph_loop.config import AuthConfig


class SandboxBackend:
    """Wrap a backend and execute prompts through a Docker image."""

    def __init__(self, inner: Backend, auth_config: AuthConfig, workspace_dir: str) -> None:
        self.inner = inner
        self.auth = auth_config
        self.workspace = Path(workspace_dir).resolve()

    @property
    def name(self) -> str:
        """Return wrapped backend name."""
        return self.inner.name

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 600,
        extra_flags: list[str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        prompt_file = self._prepare_prompt_file(prompt)
        image = f"ralph-loop-{self.inner.name}"
        container_cwd = self._resolve_container_cwd(cwd)
        effective_flags = extra_flags or []

        command = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{self.workspace}:/workspace",
            "-w",
            container_cwd,
        ]

        for mount_path in self.auth.mount:
            expanded = Path(mount_path).expanduser().resolve()
            if expanded.exists():
                if mount_path.startswith("~/"):
                    container_target = Path("/home/ralph") / mount_path[2:]
                else:
                    container_target = expanded
                command.extend(["-v", f"{expanded}:{container_target}"])

        for env_name in self.auth.env:
            env_value = os.environ.get(env_name)
            if env_value is not None:
                command.extend(["-e", f"{env_name}={env_value}"])

        relative_prompt = prompt_file.relative_to(self.workspace)
        container_prompt = Path("/workspace") / relative_prompt
        command.extend([image, "execute", "--prompt-file", str(container_prompt)])
        if model:
            command.extend(["--model", model])
        command.extend(["--timeout-seconds", str(timeout_seconds)])
        for flag in effective_flags:
            command.extend(["--extra-flag", flag])

        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds + 15,
            )
            duration = time.monotonic() - start
            return ExecutionResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=duration,
                timed_out=result.returncode == 124,
            )
        except subprocess.TimeoutExpired as timeout_error:
            duration = time.monotonic() - start
            if isinstance(timeout_error.stdout, bytes):
                stdout = timeout_error.stdout.decode()
            elif isinstance(timeout_error.stdout, str):
                stdout = timeout_error.stdout
            else:
                stdout = ""

            if isinstance(timeout_error.stderr, bytes):
                stderr = timeout_error.stderr.decode()
            elif isinstance(timeout_error.stderr, str):
                stderr = timeout_error.stderr
            else:
                stderr = ""
            return ExecutionResult(
                exit_code=124,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
                timed_out=True,
            )

    def is_available(self) -> bool:
        """Return whether Docker is available and wrapped backend is available."""
        return shutil.which("docker") is not None and self.inner.is_available()

    def _prepare_prompt_file(self, prompt: str) -> Path:
        candidate = Path(prompt)
        if candidate.exists():
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.workspace)
                return resolved
            except ValueError:
                return self._write_temp_prompt(resolved.read_text(encoding="utf-8"))

        return self._write_temp_prompt(prompt)

    def _write_temp_prompt(self, prompt: str) -> Path:
        tmp_dir = self.workspace / ".ralph-tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        file_path = tmp_dir / f"sandbox-prompt-{uuid.uuid4().hex}.md"
        file_path.write_text(prompt, encoding="utf-8")
        return file_path

    def _resolve_container_cwd(self, cwd: str | None) -> str:
        if cwd is None:
            return "/workspace"

        candidate = Path(cwd).expanduser().resolve()
        try:
            relative = candidate.relative_to(self.workspace)
        except ValueError:
            return "/workspace"

        return str(Path("/workspace") / relative)
