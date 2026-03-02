from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from ralph_loop.config import RalphConfig
from ralph_loop.loop import run_loop
from ralph_loop.progress import load_progress


def test_full_loop_pass_retry_abort(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()

    _write_task(tasks_dir / "01-task.md", "Task 01", "verify-01")
    _write_task(tasks_dir / "02-task.md", "Task 02", "verify-02")
    _write_task(tasks_dir / "03-task.md", "Task 03", "verify-03")

    progress_path = workspace / "PROGRESS.yaml"
    progress_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"title": "Integration", "started": "2026-03-01", "current_phase": 1},
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "status": "not_started",
                        "tasks": [
                            {
                                "id": "01",
                                "title": "Task 01",
                                "task_file": str(tasks_dir / "01-task.md"),
                                "status": "not_started",
                                "retries": 0,
                                "verify_commands": ["verify-01"],
                                "visual_verify": None,
                                "feedback": [],
                            },
                            {
                                "id": "02",
                                "title": "Task 02",
                                "task_file": str(tasks_dir / "02-task.md"),
                                "status": "not_started",
                                "retries": 0,
                                "verify_commands": ["verify-02"],
                                "visual_verify": None,
                                "feedback": [],
                            },
                            {
                                "id": "03",
                                "title": "Task 03",
                                "task_file": str(tasks_dir / "03-task.md"),
                                "status": "not_started",
                                "retries": 0,
                                "verify_commands": ["verify-03"],
                                "visual_verify": None,
                                "feedback": [],
                            },
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    config = RalphConfig.model_validate(
        {
            "workspace_dir": str(workspace),
            "progress_file": str(progress_path),
            "task_dir": str(tasks_dir),
            "max_retries": 3,
            "pause_file": str(workspace / "PAUSE.md"),
            "backends": {
                "coder": {"engine": "codex", "timeout_seconds": 60},
                "inspector": {"engine": "copilot", "timeout_seconds": 60},
            },
            "verify_commands": [],
        }
    )

    class _Backend:
        def __init__(self, engine: str) -> None:
            self.engine = engine

        @property
        def name(self) -> str:
            return self.engine

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = model, timeout_seconds, extra_flags, cwd
            if self.engine == "copilot":
                if "Task 01" in prompt:
                    output = '{"verdict":"pass","feedback":"ok"}'
                elif "Task 02" in prompt:
                    output = '{"verdict":"pass","feedback":"ok"}'
                else:
                    output = '{"verdict":"fail","feedback":"not ready"}'
                return SimpleNamespace(exit_code=0, stdout=output, stderr="", timed_out=False)
            return SimpleNamespace(exit_code=0, stdout="coded", stderr="", timed_out=False)

        def is_available(self) -> bool:
            return True

    monkeypatch.setattr("ralph_loop.loop.get_backend", lambda engine: _Backend(engine))

    verify_counts: dict[str, int] = {"verify-01": 0, "verify-02": 0, "verify-03": 0}

    def _fake_run(command, cwd, capture_output, text, check):
        _ = cwd, capture_output, text, check
        if command[:3] == ["git", "--no-pager", "diff"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        verify_cmd = command[2]
        verify_counts[verify_cmd] += 1

        if verify_cmd == "verify-01":
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        if verify_cmd == "verify-02":
            if verify_counts[verify_cmd] == 1:
                return SimpleNamespace(returncode=1, stdout="fail", stderr="boom")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")
        return SimpleNamespace(returncode=1, stdout="fail", stderr="boom")

    monkeypatch.setattr("subprocess.run", _fake_run)

    exit_code = run_loop(config, sandbox="none")
    assert exit_code == 1

    progress = load_progress(str(progress_path))
    task_01 = progress.phases[0].tasks[0]
    task_02 = progress.phases[0].tasks[1]
    task_03 = progress.phases[0].tasks[2]

    assert task_01.status.value == "completed"
    assert task_02.status.value == "completed"
    assert task_03.status.value == "abort"

    assert len(task_02.feedback) == 1
    assert len(task_03.feedback) == 3


def test_run_loop_recovers_orphan_in_progress_task(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()

    _write_task(tasks_dir / "01-task.md", "Task 01", "verify-01")

    progress_path = workspace / "PROGRESS.yaml"
    progress_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"title": "Integration", "started": "2026-03-01", "current_phase": 1},
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "status": "in_progress",
                        "tasks": [
                            {
                                "id": "01",
                                "title": "Task 01",
                                "task_file": str(tasks_dir / "01-task.md"),
                                "status": "in_progress",
                                "retries": 0,
                                "verify_commands": ["verify-01"],
                                "visual_verify": None,
                                "feedback": [],
                            }
                        ],
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    config = RalphConfig.model_validate(
        {
            "workspace_dir": str(workspace),
            "progress_file": str(progress_path),
            "task_dir": str(tasks_dir),
            "max_retries": 3,
            "pause_file": str(workspace / "PAUSE.md"),
            "backends": {
                "coder": {"engine": "codex", "timeout_seconds": 60},
                "inspector": {"engine": "copilot", "timeout_seconds": 60},
            },
            "verify_commands": [],
        }
    )

    class _Backend:
        def __init__(self, engine: str) -> None:
            self.engine = engine

        @property
        def name(self) -> str:
            return self.engine

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = model, timeout_seconds, extra_flags, cwd
            if self.engine == "copilot":
                return SimpleNamespace(
                    exit_code=0,
                    stdout='{"verdict":"pass","feedback":"ok"}',
                    stderr="",
                    timed_out=False,
                )
            return SimpleNamespace(exit_code=0, stdout="coded", stderr="", timed_out=False)

        def is_available(self) -> bool:
            return True

    monkeypatch.setattr("ralph_loop.loop.get_backend", lambda engine: _Backend(engine))
    monkeypatch.setattr("builtins.input", lambda _: "r")

    def _fake_run(command, cwd, capture_output, text, check):
        _ = cwd, capture_output, text, check
        if command[:3] == ["git", "--no-pager", "diff"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    exit_code = run_loop(config, sandbox="none")
    assert exit_code == 0

    progress = load_progress(str(progress_path))
    task_01 = progress.phases[0].tasks[0]
    assert task_01.status.value == "completed"


def _write_task(path: Path, title: str, verify_command: str) -> None:
    path.write_text(
        f"""---
phase: 1
verify_commands:
    - {verify_command}
files_to_touch: []
files_not_to_touch: []
---

# {title}

## Description

Implement {title}.

## Acceptance Criteria

1. done

## Test Plan

1. verify
""",
        encoding="utf-8",
    )
