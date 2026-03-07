from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ralph_loop.config import AuthConfig, AuthMountConfig, ConfigNotFoundError, RalphConfig, resolve_config_path


def test_load_config(sample_workspace: Path) -> None:
    config = RalphConfig.load(str(sample_workspace / "ralph-config.yaml"))
    assert config.max_retries == 3
    assert config.get_backend("coder").engine == "codex"


def test_get_auth_unknown_engine_returns_empty(sample_workspace: Path) -> None:
    config = RalphConfig.load(str(sample_workspace / "ralph-config.yaml"))
    auth = config.get_auth("claude")
    assert auth == AuthConfig()


def test_auth_mount_accepts_explicit_source_and_target(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "auth": {
                    "codex": {
                        "mount": [
                            {
                                "source": "~/.codex",
                                "target": "~/.codex",
                            }
                        ]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    config = RalphConfig.load(str(config_path))

    auth = config.get_auth("codex")
    assert auth.mount == [AuthMountConfig(source="~/.codex", target="~/.codex")]
    assert auth.iter_mount_bindings() == [("~/.codex", "/home/ralph/.codex")]


def test_auth_mount_rejects_legacy_string_entries(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "auth": {
                    "codex": {
                        "mount": ["~/.codex"]
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        RalphConfig.load(str(config_path))


def test_review_mode_defaults_to_legacy(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump({"backends": {"coder": {"engine": "codex"}}}),
        encoding="utf-8",
    )
    config = RalphConfig.load(str(config_path))
    assert config.review_mode == "legacy"


def test_review_mode_validation_rejects_unknown_value(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "review_mode": "something_else",
                "backends": {"coder": {"engine": "codex"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        RalphConfig.load(str(config_path))


def test_runtime_guards_load_valid_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "runtime_guards": {
                    "pre_code": {
                        "command": "./scripts/ralph/preflight.sh",
                        "timeout_seconds": 120,
                        "on_failure": "pause_loop",
                    },
                    "post_code": {
                        "command": "./scripts/ralph/preflight.sh",
                        "timeout_seconds": 180,
                        "on_failure": "fail_attempt",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    config = RalphConfig.load(str(config_path))
    assert config.runtime_guards.pre_code is not None
    assert config.runtime_guards.pre_code.command == "./scripts/ralph/preflight.sh"
    assert config.runtime_guards.pre_code.timeout_seconds == 120
    assert config.runtime_guards.pre_code.on_failure == "pause_loop"
    assert config.runtime_guards.post_code is not None
    assert config.runtime_guards.post_code.on_failure == "fail_attempt"


def test_runtime_guard_timeout_must_be_positive(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "runtime_guards": {
                    "pre_code": {
                        "command": "echo ok",
                        "timeout_seconds": 0,
                        "on_failure": "pause_loop",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        RalphConfig.load(str(config_path))


def test_runtime_guard_on_failure_validation(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "runtime_guards": {
                    "pre_code": {
                        "command": "echo ok",
                        "timeout_seconds": 10,
                        "on_failure": "invalid_policy",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception):
        RalphConfig.load(str(config_path))


def test_docker_run_args_load_valid_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "docker_run_args": {
                    "default": ["--network", "bridge"],
                    "contexts": {
                        "visual": ["--cap-add=SYS_ADMIN"],
                        "review": ["--cpus=1.0"],
                    },
                    "engines": {"codex": ["--memory=2g"]},
                },
            }
        ),
        encoding="utf-8",
    )

    config = RalphConfig.load(str(config_path))

    assert config.docker_run_args.default == ["--network", "bridge"]
    assert config.docker_run_args.contexts["visual"] == ["--cap-add=SYS_ADMIN"]
    assert config.docker_run_args.contexts["review"] == ["--cpus=1.0"]
    assert config.docker_run_args.engines["codex"] == ["--memory=2g"]


def test_docker_run_args_defaults_to_empty(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump({"backends": {"coder": {"engine": "codex"}}}),
        encoding="utf-8",
    )

    config = RalphConfig.load(str(config_path))

    assert config.docker_run_args.default == []
    assert config.docker_run_args.contexts == {}
    assert config.docker_run_args.engines == {}


def test_agent_capabilities_load_and_resolve_backend_flags(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {
                    "coder": {"engine": "codex"},
                    "reviewer": {"engine": "codex"},
                },
                "agent_capabilities": {
                    "chrome-devtools": {
                        "type": "mcp",
                        "instruction": "Use Chrome MCP for visual checks when needed.",
                        "check_command": "command -v google-chrome",
                        "backend_flags": {
                            "codex": {
                                "code": ["--config", "mcp_servers.chrome-devtools=enabled"],
                                "review": ["--config", "mcp_servers.chrome-devtools=enabled"],
                            }
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    config = RalphConfig.load(str(config_path))

    capability = config.get_capability("chrome-devtools")
    assert capability.type == "mcp"
    assert capability.check_command == "command -v google-chrome"
    assert config.capability_backend_flags(
        "chrome-devtools",
        engine="codex",
        step="code",
    ) == ["--config", "mcp_servers.chrome-devtools=enabled"]
    assert config.capability_backend_flags(
        "chrome-devtools",
        engine="codex",
        step="review",
    ) == ["--config", "mcp_servers.chrome-devtools=enabled"]


def test_agent_capabilities_reject_unknown_type(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "backends": {"coder": {"engine": "codex"}},
                "agent_capabilities": {
                    "playwright": {
                        "type": "browser",
                        "instruction": "Use Playwright for UI assertions.",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception):
        RalphConfig.load(str(config_path))


def test_get_reviewer_backend_resolution_order(tmp_path: Path) -> None:
    reviewer_config = tmp_path / "reviewer.yaml"
    reviewer_config.write_text(
        yaml.safe_dump(
            {
                "backends": {
                    "coder": {"engine": "codex"},
                    "reviewer": {"engine": "codex", "model": "gpt-5.4-codex"},
                    "inspect": {"engine": "copilot", "model": "claude-opus"},
                    "inspector": {"engine": "copilot", "model": "claude-sonnet"},
                }
            }
        ),
        encoding="utf-8",
    )
    config = RalphConfig.load(str(reviewer_config))
    assert config.get_reviewer_backend().model == "gpt-5.4-codex"

    inspect_config = tmp_path / "inspect.yaml"
    inspect_config.write_text(
        yaml.safe_dump(
            {
                "backends": {
                    "coder": {"engine": "codex"},
                    "inspect": {"engine": "copilot", "model": "claude-opus"},
                    "inspector": {"engine": "copilot", "model": "claude-sonnet"},
                }
            }
        ),
        encoding="utf-8",
    )
    config = RalphConfig.load(str(inspect_config))
    assert config.get_reviewer_backend().model == "claude-opus"

    inspector_config = tmp_path / "inspector.yaml"
    inspector_config.write_text(
        yaml.safe_dump(
            {
                "backends": {
                    "coder": {"engine": "codex"},
                    "inspector": {"engine": "copilot", "model": "claude-sonnet"},
                }
            }
        ),
        encoding="utf-8",
    )
    config = RalphConfig.load(str(inspector_config))
    assert config.get_reviewer_backend().model == "claude-sonnet"


def test_get_reviewer_backend_raises_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "ralph-config.yaml"
    config_path.write_text(
        yaml.safe_dump({"backends": {"coder": {"engine": "codex"}}}),
        encoding="utf-8",
    )
    config = RalphConfig.load(str(config_path))
    with pytest.raises(KeyError):
        config.get_reviewer_backend()


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

    # Mock _get_repo_root to return tmp_path as the repo root.
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
