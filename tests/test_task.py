from __future__ import annotations

import json
from pathlib import Path

from ralph_loop.task import Task, TaskJson, validate_task_file


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


def test_task_json_load_and_validate(tmp_path: Path) -> None:
    task_file = tmp_path / "01-sample.json"
    payload = {
        "id": "01",
        "title": "Sample JSON Task",
        "phase": 1,
        "priority": "high",
        "coding": {
            "description": "Implement endpoint",
            "acceptance_criteria": ["Returns 200"],
            "files_to_touch": ["src/api.py"],
            "files_not_to_touch": ["src/core.py"],
            "constraints": ["No core changes"],
            "reference_impl": None,
        },
        "verify": {"commands": ["pytest tests/test_api.py"]},
        "visual": {
            "url": "http://localhost:3001",
            "assertion": "Header is visible",
            "reference": None,
            "viewport_width": 1280,
            "viewport_height": 720,
            "setup_commands": [],
            "teardown_commands": [],
            "acceptance_criteria": ["Header is visible"],
        },
        "inspect": {
            "acceptance_criteria": ["Returns 200"],
            "description_summary": "Implement endpoint",
        },
        "agent_capabilities": {
            "code": ["playwright"],
            "review": ["chrome-devtools"],
        },
    }
    task_file.write_text(json.dumps(payload), encoding="utf-8")

    task = TaskJson.load(task_file)
    assert task.title == "Sample JSON Task"
    assert task.verify.commands == ["pytest tests/test_api.py"]
    assert task.visual_verify is not None
    assert task.agent_capabilities.code == ["playwright"]
    assert task.agent_capabilities.review == ["chrome-devtools"]
    assert validate_task_file(task_file) == []


def test_validate_task_file_reports_invalid_json(tmp_path: Path) -> None:
    task_file = tmp_path / "broken.json"
    task_file.write_text("{invalid", encoding="utf-8")
    errors = validate_task_file(task_file)
    assert errors
    assert "Invalid JSON" in errors[0]
