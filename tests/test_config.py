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
