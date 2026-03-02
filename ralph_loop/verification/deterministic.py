from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DeterministicCommandResult:
    """Result for one deterministic verification command."""

    command: str
    exit_code: int
    stdout: str
    stderr: str


def run_verify_commands(
    verify_commands: list[str], workspace_dir: str, max_output_chars: int = 4000
) -> list[DeterministicCommandResult]:
    """Run all trusted verify commands with bash -lc and collect all results."""
    results: list[DeterministicCommandResult] = []
    workspace = Path(workspace_dir)

    for command in verify_commands:
        completed = subprocess.run(
            ["bash", "-lc", command],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            check=False,
        )
        results.append(
            DeterministicCommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=_truncate_tail(completed.stdout, max_output_chars),
                stderr=_truncate_tail(completed.stderr, max_output_chars),
            )
        )

    return results


def _truncate_tail(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[-max_chars:]
