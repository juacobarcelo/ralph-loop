from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Template
from pydantic import BaseModel, ValidationError

from ralph_loop.backends.base import Backend
from ralph_loop.task import Task
from ralph_loop.verification.deterministic import DeterministicCommandResult


class InspectorVerdict(BaseModel):
    """Normalized inspector verdict schema."""

    verdict: str
    feedback: str


@dataclass
class AIInspectionResult:
    """Result of AI inspection phase."""

    verdict: str
    feedback: str
    raw_output: str


def run_ai_inspection(
    *,
    task: Task,
    workspace_dir: str,
    inspector_backend: Backend,
    model: str | None,
    timeout_seconds: int,
    extra_flags: list[str],
    verify_results: list[DeterministicCommandResult] | None = None,
    project_instructions: str | None = None,
    contract_content: str | None = None,
) -> AIInspectionResult:
    """Run inspection prompt against backend and return pass/fail with feedback."""
    git_diff = _capture_git_diff(workspace_dir)
    prompt = _render_inspector_prompt(
        task=task,
        git_diff=git_diff,
        verify_results=verify_results,
        project_instructions=project_instructions,
        contract_content=contract_content,
    )

    response = inspector_backend.execute(
        prompt=prompt,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags,
        cwd=workspace_dir,
    )
    raw_output = response.stdout.strip()
    parsed = _parse_or_normalize_inspector_output(
        raw_output=raw_output,
        inspector_backend=inspector_backend,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags,
        cwd=workspace_dir,
    )
    return AIInspectionResult(
        verdict=parsed.verdict, feedback=parsed.feedback, raw_output=raw_output
    )


def _capture_git_diff(workspace_dir: str) -> str:
    completed = subprocess.run(
        ["git", "--no-pager", "diff"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout


def _render_inspector_prompt(
    *,
    task: Task,
    git_diff: str,
    verify_results: list[DeterministicCommandResult] | None,
    project_instructions: str | None,
    contract_content: str | None,
) -> str:
    template_path = Path(__file__).resolve().parents[1] / "prompts" / "inspector.md.j2"
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.render(
        task=task,
        git_diff=git_diff,
        verify_results=verify_results,
        project_instructions=project_instructions,
        contract_content=contract_content,
    )


def _parse_or_normalize_inspector_output(
    *,
    raw_output: str,
    inspector_backend: Backend,
    model: str | None,
    timeout_seconds: int,
    extra_flags: list[str],
    cwd: str,
) -> InspectorVerdict:
    direct = _try_parse_verdict(raw_output)
    if direct is not None:
        return direct

    normalization_prompt = (
        "Normalize the following inspector output into strict JSON with schema "
        '{"verdict":"pass|fail","feedback":"string"}. '
        "Return JSON only. If uncertain, set verdict to fail.\n\n"
        f"RAW OUTPUT:\n{raw_output}"
    )
    normalized = inspector_backend.execute(
        prompt=normalization_prompt,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags,
        cwd=cwd,
    )
    normalized_parsed = _try_parse_verdict(normalized.stdout)
    if normalized_parsed is not None:
        return normalized_parsed

    fallback_feedback = raw_output.strip() or "Inspector output could not be normalized"
    return InspectorVerdict(verdict="fail", feedback=fallback_feedback)


def _try_parse_verdict(payload: str) -> InspectorVerdict | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    try:
        parsed = InspectorVerdict.model_validate(data)
    except ValidationError:
        return None

    verdict = parsed.verdict.strip().lower()
    if verdict not in {"pass", "fail"}:
        return None

    return InspectorVerdict(verdict=verdict, feedback=parsed.feedback.strip())
