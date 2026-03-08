from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ralph_loop.config import VisualVerifyConfig


class CodingSection(BaseModel):
    """Coding instructions embedded in a task JSON file."""

    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_to_touch: list[str] = Field(default_factory=list)
    files_not_to_touch: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    reference_impl: str | None = None


class VerifySection(BaseModel):
    """Deterministic verification commands for a task."""

    commands: list[str] = Field(default_factory=list)


class VisualSection(BaseModel):
    """Visual verification settings for a task."""

    url: str
    assertion: str
    reference: str | None = None
    viewport_width: int = 1280
    viewport_height: int = 720
    acceptance_criteria: list[str] = Field(default_factory=list)

    def to_visual_verify_config(self) -> VisualVerifyConfig:
        return VisualVerifyConfig(
            url=self.url,
            assertion=self.assertion,
            reference=self.reference,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
        )


class InspectSection(BaseModel):
    """Inspector context for a task."""

    acceptance_criteria: list[str] = Field(default_factory=list)
    description_summary: str = ""


class ReviewSection(BaseModel):
    """Reviewer-facing context for runtime, UI, or service validation."""

    acceptance_criteria: list[str] = Field(default_factory=list)
    description_summary: str = ""
    focus: list[str] = Field(default_factory=list)
    service_urls: list[str] = Field(default_factory=list)
    runtime_expectations: list[str] = Field(default_factory=list)


class AgentCapabilitiesSection(BaseModel):
    """Task-level capability IDs to expose to each agent step."""

    code: list[str] = Field(default_factory=list)
    review: list[str] = Field(default_factory=list)


class TaskJson(BaseModel):
    """Single-file JSON task contract with stage-scoped sections."""

    id: str
    title: str
    phase: int
    priority: str = "medium"
    coding: CodingSection
    verify: VerifySection = Field(default_factory=VerifySection)
    review: ReviewSection = Field(default_factory=ReviewSection)
    visual: VisualSection | None = None
    inspect: InspectSection | None = None
    agent_capabilities: AgentCapabilitiesSection = Field(default_factory=AgentCapabilitiesSection)

    @classmethod
    def load(cls, path: str | Path) -> TaskJson:
        task_path = Path(path)
        payload = json.loads(task_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Task JSON root must be an object")
        return cls.model_validate(payload)

    def save(self, path: str | Path) -> None:
        task_path = Path(path)
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(
            json.dumps(
                self.model_dump(mode="json", exclude_none=True),
                indent=2,
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )

    @property
    def description(self) -> str:
        return self.coding.description

    @property
    def acceptance_criteria(self) -> list[str]:
        return self.coding.acceptance_criteria

    @property
    def reference_impl(self) -> str | None:
        return self.coding.reference_impl

    @property
    def constraints(self) -> list[str]:
        return self.coding.constraints

    @property
    def files_to_touch(self) -> list[str]:
        return self.coding.files_to_touch

    @property
    def files_not_to_touch(self) -> list[str]:
        return self.coding.files_not_to_touch

    @property
    def visual_verify(self) -> VisualVerifyConfig | None:
        if self.visual is None:
            return None
        return self.visual.to_visual_verify_config()

    def to_legacy_task(self, file_path: str | Path) -> Task:
        """Build a legacy Task view for callers that still require the markdown model."""
        return Task(
            frontmatter=TaskFrontmatter(
                phase=self.phase,
                priority=self.priority,
                verify_commands=self.verify.commands,
                visual_verify=self.visual_verify,
                contract_file=None,
                files_to_touch=self.files_to_touch,
                files_not_to_touch=self.files_not_to_touch,
            ),
            body="",
            file_path=str(file_path),
            title=self.title,
            description=self.description,
            acceptance_criteria=self.acceptance_criteria,
            test_plan="",
            reference_impl=self.reference_impl,
            constraints=self.constraints,
        )


def validate_task_file(path: str | Path) -> list[str]:
    """Validate a JSON task file and return error messages."""
    task_path = Path(path)
    errors: list[str] = []
    if not task_path.exists():
        return [f"Task file not found: {task_path}"]

    try:
        payload: Any = json.loads(task_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON in {task_path}: {exc}"]

    if not isinstance(payload, dict):
        return [f"Invalid JSON root in {task_path}: expected object"]

    try:
        TaskJson.model_validate(payload)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Task schema error in {task_path}: {exc}")
    return errors


class TaskFrontmatter(BaseModel):
    """YAML frontmatter parsed from a task file."""

    phase: int
    priority: str = "medium"
    verify_commands: list[str] = Field(
        default_factory=list,
        description=(
            "Optional deterministic host-side checks for this task. Keep empty by "
            "default in unified_agent loops unless a task-local command is "
            "explicitly justified."
        ),
    )
    visual_verify: VisualVerifyConfig | None = None
    contract_file: str | None = None
    files_to_touch: list[str] = Field(default_factory=list)
    files_not_to_touch: list[str] = Field(default_factory=list)


class Task(BaseModel):
    """Task model composed of frontmatter and markdown body."""

    frontmatter: TaskFrontmatter
    body: str
    file_path: str
    title: str
    description: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    test_plan: str = ""
    reference_impl: str | None = None
    constraints: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> Task:
        """Load and parse a task markdown file."""
        file_path = Path(path)
        content = file_path.read_text(encoding="utf-8")

        frontmatter_data, body = _split_frontmatter(content)
        frontmatter = TaskFrontmatter.model_validate(frontmatter_data)

        title = _extract_title(body)
        description = _extract_section(body, "Description")
        acceptance_criteria = _extract_list_section(body, "Acceptance Criteria")
        test_plan = _extract_section(body, "Test Plan")
        reference_impl = _extract_section(body, "Reference Implementation") or None
        constraints = _extract_list_section(body, "Constraints")

        return cls(
            frontmatter=frontmatter,
            body=body,
            file_path=str(file_path),
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            test_plan=test_plan,
            reference_impl=reference_impl,
            constraints=constraints,
        )

    def get_verify_commands(self, defaults: list[str]) -> list[str]:
        """Return task-specific verify commands or provided defaults.

        Unified-agent loops should normally keep both task and config defaults
        empty unless a task-local deterministic command was requested on purpose.
        """
        return self.frontmatter.verify_commands or defaults


def _split_frontmatter(content: str) -> tuple[dict[str, object], str]:
    if not content.startswith("---\n"):
        return {}, content

    marker = "\n---\n"
    end = content.find(marker, 4)
    if end == -1:
        return {}, content

    raw_frontmatter = content[4:end]
    body = content[end + len(marker) :].lstrip("\n")

    parsed = yaml.safe_load(raw_frontmatter)
    if parsed is None:
        return {}, body
    if not isinstance(parsed, dict):
        raise ValueError("Task frontmatter must be a mapping")
    return parsed, body


def _extract_title(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return "Untitled Task"


def _extract_section(body: str, section_name: str) -> str:
    lines = body.splitlines()
    target_header = f"## {section_name}"

    start_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == target_header:
            start_index = index + 1
            break

    if start_index is None:
        return ""

    section_lines: list[str] = []
    for line in lines[start_index:]:
        if line.strip().startswith("## "):
            break
        section_lines.append(line)

    return "\n".join(section_lines).strip()


def _extract_list_section(body: str, section_name: str) -> list[str]:
    raw = _extract_section(body, section_name)
    if not raw:
        return []

    items: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
            continue
        numeric = stripped.split(". ", 1)
        if len(numeric) == 2 and numeric[0].isdigit():
            items.append(numeric[1].strip())

    return items
