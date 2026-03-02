from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


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
    def load(cls, path: str) -> RalphConfig:
        """Load config from YAML and resolve relative paths."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}

        if not isinstance(raw, dict):
            raise ValueError("Config YAML root must be a mapping")

        base_dir = config_path.parent.resolve()
        raw.setdefault("progress_file", "PROGRESS.yaml")
        raw.setdefault("task_dir", "tasks/")
        raw.setdefault("pause_file", "PAUSE.md")
        raw.setdefault("workspace_dir", str(base_dir))

        for key in ("progress_file", "task_dir", "pause_file", "project_instructions"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                candidate = Path(value)
                if not candidate.is_absolute():
                    raw[key] = str((base_dir / candidate).resolve())

        workspace_dir = raw.get("workspace_dir")
        if isinstance(workspace_dir, str) and workspace_dir:
            workspace = Path(workspace_dir)
            if not workspace.is_absolute():
                raw["workspace_dir"] = str((base_dir / workspace).resolve())
        else:
            raw["workspace_dir"] = str(base_dir)

        return cls.model_validate(raw)

    def get_backend(self, role: str) -> BackendConfig:
        """Get backend config for a role."""
        if role not in self.backends:
            raise KeyError(f"Backend role not configured: {role}")
        return self.backends[role]

    def get_auth(self, engine: str) -> AuthConfig:
        """Get auth config for engine or return empty config."""
        return self.auth.get(engine, AuthConfig())
