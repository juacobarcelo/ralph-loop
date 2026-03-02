from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ralph_loop.config import AuthConfig, ConfigNotFoundError, RalphConfig, resolve_config_path


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


# ---------------------------------------------------------------------------
# resolve_config_path tests
# ---------------------------------------------------------------------------

_MINIMAL_CONFIG = yaml.safe_dump({"backends": {"coder": {"engine": "codex"}}})


def test_resolve_explicit_config_flag(sample_workspace: Path) -> None:
    """Scenario 1: --config is passed — handled by Click, not resolve_config_path.

    This test verifies that RalphConfig.load still works with an explicit path.
    """
    config = RalphConfig.load(str(sample_workspace / "ralph-config.yaml"))
    assert config.get_backend("coder").engine == "codex"


def test_resolve_ralph_config_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 2: RALPH_CONFIG env var takes precedence."""
    env_config = tmp_path / "env-config.yaml"
    env_config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    monkeypatch.setenv("RALPH_CONFIG", str(env_config))
    monkeypatch.chdir(tmp_path / "nonexistent_cwd" if False else tmp_path)

    result = resolve_config_path()
    assert result == str(env_config)


def test_resolve_local_ralph_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 3: ralph-config.yaml in CWD is found."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    local_config = tmp_path / "ralph-config.yaml"
    local_config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    result = resolve_config_path()
    assert result == str(local_config.resolve())


def test_resolve_repo_root_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 4: ralph-config.yaml at host repo root (submodule case)."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)

    # Simulate CWD inside a subdirectory (no local config).
    subdir = tmp_path / "sub" / "module"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)

    # Place config at the "repo root".
    repo_root_config = tmp_path / "ralph-config.yaml"
    repo_root_config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    # Mock submodule host root to return tmp_path as the repo root.
    monkeypatch.setattr("ralph_loop.config._get_superproject_root", lambda: tmp_path.resolve())
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: tmp_path.resolve())

    result = resolve_config_path()
    assert result == str(repo_root_config.resolve())


def test_resolve_global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 5: Falls back to global ~/.config/ralph-loop/config.yaml."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)

    # CWD with no config.
    cwd = tmp_path / "empty_cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    monkeypatch.setattr("ralph_loop.config._get_superproject_root", lambda: None)
    # No repo root.
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: None)

    # Set up a fake global config.
    global_dir = tmp_path / "fakehome" / ".config" / "ralph-loop"
    global_dir.mkdir(parents=True)
    global_config = global_dir / "config.yaml"
    global_config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    monkeypatch.setattr("ralph_loop.config.GLOBAL_CONFIG_PATH", Path(str(global_config)))

    result = resolve_config_path()
    assert result == str(global_config.resolve())


def test_resolve_no_config_raises_with_attempted_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 6: No config anywhere — raises ConfigNotFoundError with paths."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)

    cwd = tmp_path / "no_config"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    monkeypatch.setattr("ralph_loop.config._get_superproject_root", lambda: None)
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: None)

    # Point global to a non-existent path.
    fake_global = tmp_path / "nope" / "config.yaml"
    monkeypatch.setattr("ralph_loop.config.GLOBAL_CONFIG_PATH", Path(str(fake_global)))

    with pytest.raises(ConfigNotFoundError) as exc_info:
        resolve_config_path()

    error = exc_info.value
    assert len(error.attempted) >= 2
    assert str(cwd.resolve() / "ralph-config.yaml") in error.attempted
    assert str(fake_global.resolve()) in error.attempted
    assert "No ralph-loop config file found" in str(error)


def test_resolve_repo_root_skipped_when_equals_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repo root step is skipped when it equals CWD (already checked in step 2)."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr("ralph_loop.config._get_superproject_root", lambda: None)
    # _get_repo_root returns CWD itself.
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: tmp_path.resolve())

    # Point global to non-existent so we get an error with the attempted list.
    fake_global = tmp_path / "nope" / "config.yaml"
    monkeypatch.setattr("ralph_loop.config.GLOBAL_CONFIG_PATH", Path(str(fake_global)))

    with pytest.raises(ConfigNotFoundError) as exc_info:
        resolve_config_path()

    # The repo root path should NOT appear in attempted (it was skipped).
    attempted = exc_info.value.attempted
    repo_candidate = str(tmp_path.resolve() / "ralph-config.yaml")
    assert attempted.count(repo_candidate) == 1  # only from step 2, not step 3


def test_resolve_git_not_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_get_repo_root returns None when git is not installed."""
    from ralph_loop.config import _get_repo_root

    def _fake_run(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("git not found")

    monkeypatch.setattr("subprocess.run", _fake_run)
    assert _get_repo_root() is None


def test_resolve_submodule_ignores_local_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When inside a submodule, local config is ignored in favor of host config."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)

    submodule_dir = tmp_path / "submodule"
    submodule_dir.mkdir(parents=True)
    monkeypatch.chdir(submodule_dir)

    submodule_config = submodule_dir / "ralph-config.yaml"
    submodule_config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    host_config = tmp_path / "ralph-config.yaml"
    host_config.write_text(_MINIMAL_CONFIG, encoding="utf-8")

    monkeypatch.setattr("ralph_loop.config._get_superproject_root", lambda: tmp_path.resolve())
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: tmp_path.resolve())

    result = resolve_config_path()
    assert result == str(host_config.resolve())
