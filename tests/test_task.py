from __future__ import annotations

from pathlib import Path

from ralph_loop.task import Task


def test_task_load_parses_frontmatter_and_sections(tmp_path: Path) -> None:
    task_file = tmp_path / "01-sample.md"
    task_file.write_text(
        """---
phase: 1
priority: high
verify_commands:
  - pytest tests/test_api.py
visual_verify: null
contract_file: null
files_to_touch:
  - src/api.py
files_not_to_touch:
  - src/core.py
---

# Task 01: Sample

## Description

Implement API endpoint.

## Acceptance Criteria

1. Endpoint returns 200
2. Endpoint validates payload

## Test Plan

1. Run API tests

## Constraints

- Do not modify core module

## Reference Implementation

See src/existing.py
""",
        encoding="utf-8",
    )

    task = Task.load(str(task_file))

    assert task.frontmatter.phase == 1
    assert task.frontmatter.priority == "high"
    assert task.title == "Task 01: Sample"
    assert task.description == "Implement API endpoint."
    assert task.acceptance_criteria == ["Endpoint returns 200", "Endpoint validates payload"]
    assert "Run API tests" in task.test_plan
    assert task.reference_impl == "See src/existing.py"
    assert task.constraints == ["Do not modify core module"]


def test_get_verify_commands_fallback(tmp_path: Path) -> None:
    task_file = tmp_path / "02-fallback.md"
    task_file.write_text(
        """---
phase: 1
verify_commands: []
---

# Task 02: Fallback

## Description

Do work.
""",
        encoding="utf-8",
    )

    task = Task.load(str(task_file))
    assert task.get_verify_commands(["pytest tests/"]) == ["pytest tests/"]
