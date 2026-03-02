from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def sample_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    tasks_dir = workspace / "tasks"
    tasks_dir.mkdir()

    task_file = tasks_dir / "01-task.md"
    task_file.write_text("# task", encoding="utf-8")

    progress = {
        "meta": {"title": "Demo", "started": "2026-03-01", "current_phase": 1},
        "phases": [
            {
                "id": 1,
                "name": "Phase 1",
                "status": "not_started",
                "tasks": [
                    {
                        "id": "01",
                        "title": "Task 01",
                        "task_file": str(task_file),
                        "status": "not_started",
                        "retries": 0,
                        "verify_commands": [],
                        "visual_verify": None,
                        "feedback": [],
                    }
                ],
            }
        ],
    }
    progress_path = workspace / "PROGRESS.yaml"
    progress_path.write_text(yaml.safe_dump(progress, sort_keys=False), encoding="utf-8")

    config = {
        "progress_file": str(progress_path),
        "task_dir": str(tasks_dir),
        "max_retries": 3,
        "workspace_dir": str(workspace),
        "pause_file": str(workspace / "PAUSE.md"),
        "backends": {
            "coder": {"engine": "codex", "model": "gpt-5.3-codex", "timeout_seconds": 600},
            "inspector": {"engine": "copilot", "timeout_seconds": 300},
        },
        "auth": {"codex": {"env": ["OPENAI_API_KEY"], "mount": []}},
    }
    config_path = workspace / "ralph-config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    return workspace
