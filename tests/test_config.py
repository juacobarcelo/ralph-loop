from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ralph_loop.config import AuthConfig, RalphConfig


def test_load_config(sample_workspace: Path) -> None:
    config = RalphConfig.load(str(sample_workspace / "ralph-config.yaml"))
    assert config.max_retries == 3
    assert config.get_backend("coder").engine == "codex"


def test_get_auth_unknown_engine_returns_empty(sample_workspace: Path) -> None:
    config = RalphConfig.load(str(sample_workspace / "ralph-config.yaml"))
    auth = config.get_auth("claude")
    assert auth == AuthConfig()


def test_load_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        RalphConfig.load("missing.yaml")


def test_max_retries_must_be_positive(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "max_retries": 0,
                "backends": {"coder": {"engine": "codex"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        RalphConfig.load(str(config_path))


def test_load_resolves_runtime_defaults_from_loop_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "global-config.yaml"
    loop_dir = tmp_path / "loops" / "demo"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {
                    "coder": {"engine": "codex"},
                    "inspector": {"engine": "copilot"},
                },
                "verify_commands": ["pytest -q tests"],
            }
        ),
        encoding="utf-8",
    )

    config = RalphConfig.load(str(config_path), loop_dir=str(loop_dir))

    assert config.progress_file == str((loop_dir / "PROGRESS.yaml").resolve())
    assert config.task_dir == str((loop_dir / "tasks").resolve())
    assert config.pause_file == str((loop_dir / "PAUSE.md").resolve())
    assert config.workspace_dir == str((loop_dir / "product").resolve())


def test_load_discovers_project_instructions_from_loop_ancestors(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    loop_dir = root / ".ralph-loop" / "demo"
    loop_dir.mkdir(parents=True)
    instructions = root / "AGENTS.md"
    instructions.write_text("# instructions", encoding="utf-8")

    config_path = tmp_path / "global-config.yaml"
    config_path.write_text(
        yaml.safe_dump({"backends": {"coder": {"engine": "codex"}}}),
        encoding="utf-8",
    )

    config = RalphConfig.load(str(config_path), loop_dir=str(loop_dir))

    assert config.project_instructions == str(instructions.resolve())
