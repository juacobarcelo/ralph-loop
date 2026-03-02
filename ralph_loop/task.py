from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ralph_loop.config import VisualVerifyConfig


class TaskFrontmatter(BaseModel):
    """YAML frontmatter parsed from a task file."""

    phase: int
    priority: str = "medium"
    verify_commands: list[str] = Field(default_factory=list)
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
        """Return task-specific verify commands or provided defaults."""
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
