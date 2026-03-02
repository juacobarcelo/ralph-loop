from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


PROJECT_INSTRUCTIONS_FILENAMES: tuple[str, ...] = ("AGENTS.md", "CLAUDE.md", "COPILOT.md")
LOCAL_CONFIG_FILENAME: str = "ralph-config.yaml"
GLOBAL_CONFIG_PATH: Path = Path("~/.config/ralph-loop/config.yaml")


def _get_repo_root() -> Path | None:
    """Return the host repo root, preferring the superproject when inside a submodule.

    Returns ``None`` if *git* is unavailable or CWD is not inside a git repository.
    """
    try:
        # If we are a submodule, this returns the host (superproject) root.
        result = subprocess.run(
            ["git", "rev-parse", "--show-superproject-working-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        superproject = result.stdout.strip() if result.returncode == 0 else ""
        if superproject:
            return Path(superproject).resolve()

        # Not a submodule — fall back to the repo root.
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except FileNotFoundError:
        # git is not installed.
        pass
    return None


class ConfigNotFoundError(FileNotFoundError):
    """Raised when no configuration file can be found in any searched location."""

    def __init__(self, attempted: list[str]) -> None:
        paths_list = "\n  - ".join(attempted)
        super().__init__(f"No ralph-loop config file found. Searched (in order):\n  - {paths_list}")
        self.attempted = attempted


def resolve_config_path() -> str:
    """Resolve the config file path using the precedence chain.

    Precedence (highest to lowest):
      1. ``RALPH_CONFIG`` environment variable.
      2. ``ralph-config.yaml`` in the current working directory.
      3. ``ralph-config.yaml`` at the host/repo root (submodule-aware).
      4. ``~/.config/ralph-loop/config.yaml`` (global fallback).

    Raises:
        ConfigNotFoundError: when no config file is found.
    """
    attempted: list[str] = []

    # 1. RALPH_CONFIG env var
    env_path = os.environ.get("RALPH_CONFIG")
    if env_path:
        return env_path  # trust the user; existence checked by RalphConfig.load

    # 2. Local ralph-config.yaml in CWD
    cwd = Path.cwd().resolve()
    local_candidate = cwd / LOCAL_CONFIG_FILENAME
    attempted.append(str(local_candidate))
    if local_candidate.is_file():
        return str(local_candidate)

    # 3. Host repo root ralph-config.yaml
    repo_root = _get_repo_root()
    if repo_root and repo_root != cwd:
        repo_candidate = repo_root / LOCAL_CONFIG_FILENAME
        attempted.append(str(repo_candidate))
        if repo_candidate.is_file():
            return str(repo_candidate)

    # 4. Global config
    global_candidate = GLOBAL_CONFIG_PATH.expanduser().resolve()
    attempted.append(str(global_candidate))
    if global_candidate.is_file():
        return str(global_candidate)

    raise ConfigNotFoundError(attempted)


class BackendConfig(BaseModel):
    """Configuration for a single AI backend role."""

    engine: str
    model: str | None = None
    extra_flags: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600
    max_turns: int | None = None
    budget_usd: float | None = None


class AuthConfig(BaseModel):
    """Auth credentials forwarding for Docker containers."""

    env: list[str] = Field(default_factory=list)
    mount: list[str] = Field(default_factory=list)


class VisualVerifyConfig(BaseModel):
    """Configuration for visual screenshot verification of a task."""

    type: str = "screenshot"
    url: str
    reference: str | None = None
    assertion: str
    viewport_width: int = 1280
    viewport_height: int = 720
    setup_commands: list[str] = Field(default_factory=list)
    teardown_commands: list[str] = Field(default_factory=list)


class RalphConfig(BaseModel):
    """Root configuration loaded from YAML."""

    progress_file: str = "PROGRESS.yaml"
    task_dir: str = "tasks/"
    max_retries: int = 3
    pause_file: str = "PAUSE.md"
    workspace_dir: str = "."

    backends: dict[str, BackendConfig]
    verify_commands: list[str] = Field(default_factory=list)
    auth: dict[str, AuthConfig] = Field(default_factory=dict)
    project_instructions: str | None = None

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_retries must be >= 1")
        return value

    @classmethod
    def load(cls, path: str, *, loop_dir: str | None = None) -> RalphConfig:
        """Load config from YAML and resolve runtime defaults for a loop directory."""
        config_path = Path(path).expanduser().resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}

        if not isinstance(raw, dict):
            raise ValueError("Config YAML root must be a mapping")

        base_dir = config_path.parent
        resolved_loop_dir = cls._resolve_loop_dir(loop_dir, base_dir)

        raw.setdefault("progress_file", str((resolved_loop_dir / "PROGRESS.yaml").resolve()))
        raw.setdefault("task_dir", str((resolved_loop_dir / "tasks").resolve()))
        raw.setdefault("pause_file", str((resolved_loop_dir / "PAUSE.md").resolve()))
        raw.setdefault("workspace_dir", str((resolved_loop_dir / "product").resolve()))

        for key in ("progress_file", "task_dir", "pause_file", "workspace_dir"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                raw[key] = str(cls._resolve_path(value, base_dir))

        project_instructions = raw.get("project_instructions")
        if isinstance(project_instructions, str) and project_instructions:
            raw["project_instructions"] = str(cls._resolve_path(project_instructions, base_dir))
        else:
            raw["project_instructions"] = cls._discover_project_instructions(resolved_loop_dir)

        return cls.model_validate(raw)

    @staticmethod
    def _resolve_loop_dir(loop_dir: str | None, base_dir: Path) -> Path:
        if loop_dir:
            return Path(loop_dir).expanduser().resolve()
        return base_dir.resolve()

    @staticmethod
    def _resolve_path(value: str, base_dir: Path) -> Path:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (base_dir / candidate).resolve()

    @staticmethod
    def _discover_project_instructions(loop_dir: Path) -> str | None:
        for current in [loop_dir, *loop_dir.parents]:
            for filename in PROJECT_INSTRUCTIONS_FILENAMES:
                candidate = current / filename
                if candidate.exists() and candidate.is_file():
                    return str(candidate.resolve())
        return None

    def get_backend(self, role: str) -> BackendConfig:
        """Get backend config for a role."""
        if role not in self.backends:
            raise KeyError(f"Backend role not configured: {role}")
        return self.backends[role]

    def get_auth(self, engine: str) -> AuthConfig:
        """Get auth config for engine or return empty config."""
        return self.auth.get(engine, AuthConfig())
