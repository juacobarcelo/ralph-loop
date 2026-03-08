from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml
from jinja2 import Template
from pydantic import BaseModel, Field, ValidationError

from ralph_loop.backends import get_backend
from ralph_loop.backends.base import ExecutionResult
from ralph_loop.config import (
    AgentCapabilityConfig,
    BackendConfig,
    ConfigNotFoundError,
    RalphConfig,
    RuntimeGuardConfig,
    VisualVerifyConfig,
    resolve_config_path,
)
from ralph_loop.loop import run_loop
from ralph_loop.progress import (
    FeedbackEntry,
    FeedbackSource,
    PhaseStatus,
    Progress,
    TaskStatus,
    complete_task,
    fail_task,
    find_in_progress_tasks,
    find_task,
    load_progress,
    lock_task,
    recover_in_progress_task,
    save_progress,
    select_next_task,
)
from ralph_loop.feedback import filter_feedback_for_coder, format_feedback_for_prompt
from ralph_loop.task import Task, TaskJson, validate_task_file
from ralph_loop.verification.visual import run_visual_verification


def _default_config_path() -> str:
    """Resolve config path using the precedence chain.

    Called by Click as the default for ``--config`` when no explicit value is given.
    """
    try:
        return resolve_config_path()
    except ConfigNotFoundError as exc:
        raise click.UsageError(str(exc)) from exc


def _load_config(config_path: str, loop_dir: str = ".") -> RalphConfig:
    return RalphConfig.load(config_path, loop_dir=loop_dir)


def _load_config_and_progress(
    config_path: str, loop_dir: str = "."
) -> tuple[RalphConfig, Progress]:
    config = _load_config(config_path, loop_dir)
    progress = load_progress(config.progress_file)
    return config, progress


class GeneratedTask(BaseModel):
    """Task generated from a plan document."""

    id: str
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    test_plan: str = ""
    priority: str = "medium"
    verify_commands: list[str] = Field(default_factory=list)
    review: dict[str, Any] = Field(default_factory=dict)
    files_to_touch: list[str] = Field(default_factory=list)
    files_not_to_touch: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    reference_impl: str | None = None
    agent_capabilities: dict[str, list[str]] = Field(default_factory=dict)

    def to_task_json(self, *, phase_id: int, task_id: str) -> TaskJson:
        summary_source = (self.description or self.title).strip()
        code_capabilities: list[str] = []
        review_capabilities: list[str] = []
        if isinstance(self.agent_capabilities, dict):
            raw_code = self.agent_capabilities.get("code")
            if isinstance(raw_code, list):
                code_capabilities = [
                    item.strip() for item in raw_code if isinstance(item, str) and item.strip()
                ]
            raw_review = self.agent_capabilities.get("review")
            if isinstance(raw_review, list):
                review_capabilities = [
                    item.strip() for item in raw_review if isinstance(item, str) and item.strip()
                ]

        raw_focus = self.review.get("focus") if isinstance(self.review, dict) else None
        review_focus = (
            [item.strip() for item in raw_focus if isinstance(item, str) and item.strip()]
            if isinstance(raw_focus, list)
            else []
        )

        raw_service_urls = (
            self.review.get("service_urls") if isinstance(self.review, dict) else None
        )
        review_service_urls = (
            [item.strip() for item in raw_service_urls if isinstance(item, str) and item.strip()]
            if isinstance(raw_service_urls, list)
            else []
        )

        raw_runtime_expectations = (
            self.review.get("runtime_expectations") if isinstance(self.review, dict) else None
        )
        review_runtime_expectations = (
            [
                item.strip()
                for item in raw_runtime_expectations
                if isinstance(item, str) and item.strip()
            ]
            if isinstance(raw_runtime_expectations, list)
            else []
        )

        return TaskJson.model_validate(
            {
                "id": task_id,
                "title": self.title,
                "phase": phase_id,
                "priority": self.priority,
                "coding": {
                    "description": self.description,
                    "acceptance_criteria": self.acceptance_criteria,
                    "files_to_touch": self.files_to_touch,
                    "files_not_to_touch": self.files_not_to_touch,
                    "constraints": self.constraints,
                    "reference_impl": self.reference_impl,
                },
                "verify": {"commands": self.verify_commands},
                "review": {
                    "acceptance_criteria": self.acceptance_criteria,
                    "description_summary": summary_source[:200],
                    "focus": review_focus,
                    "service_urls": review_service_urls,
                    "runtime_expectations": review_runtime_expectations,
                },
                "agent_capabilities": {
                    "code": code_capabilities,
                    "review": review_capabilities,
                },
            }
        )


class GeneratedPhase(BaseModel):
    """Phase generated from a plan document."""

    id: int
    name: str
    tasks: list[GeneratedTask] = Field(default_factory=list)


class GeneratedPlan(BaseModel):
    """Structured output expected from the plan-to-tasks generator."""

    title: str
    phases: list[GeneratedPhase] = Field(default_factory=list)


@dataclass
class _InitContext:
    source_path: Path
    config: RalphConfig


@dataclass
class _Paths:
    tmp_dir: Path
    iteration_path: Path
    coder_prompt_path: Path
    inspector_prompt_path: Path
    reviewer_prompt_path: Path
    commit_prompt_path: Path


def _runtime_paths(config: RalphConfig) -> _Paths:
    workspace = Path(config.workspace_dir)
    tmp_dir = workspace / ".ralph-tmp"
    return _Paths(
        tmp_dir=tmp_dir,
        iteration_path=tmp_dir / "iteration-state.json",
        coder_prompt_path=tmp_dir / "coder-prompt.md",
        inspector_prompt_path=tmp_dir / "inspector-prompt.md",
        reviewer_prompt_path=tmp_dir / "reviewer-prompt.md",
        commit_prompt_path=tmp_dir / "commit-prompt.md",
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_iteration_state(iteration_path: Path) -> dict[str, Any] | None:
    if not iteration_path.exists():
        return None
    payload = json.loads(iteration_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    return payload


def _save_iteration_state(iteration_path: Path, state: dict[str, Any]) -> None:
    iteration_path.parent.mkdir(parents=True, exist_ok=True)
    iteration_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_iteration_state(iteration_path: Path) -> None:
    if iteration_path.exists():
        iteration_path.unlink()


def _load_optional_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    candidate = Path(path_value).expanduser()
    if not candidate.exists() or not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


def _extract_step_result(step_result_path: str | None) -> dict[str, Any] | None:
    if not step_result_path:
        return None
    path = Path(step_result_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        path.unlink()
    except OSError:
        pass
    if not isinstance(data, dict):
        return None
    return data


def _resolve_task_file_path(task_file: str, config: RalphConfig) -> Path | None:
    candidate = Path(task_file).expanduser()
    if candidate.exists():
        return candidate.resolve()

    fallback = Path(config.task_dir) / candidate.name
    if fallback.exists():
        return fallback.resolve()

    return None


def _load_task_document(task_progress: Any, config: RalphConfig) -> Task | TaskJson | None:
    task_path = _resolve_task_file_path(str(task_progress.task_file), config)
    if task_path is None:
        return None
    try:
        if task_path.suffix.lower() == ".json":
            return TaskJson.load(task_path)
        return Task.load(str(task_path))
    except Exception:  # noqa: BLE001
        return None


def _task_verify_commands(task_progress: Any, config: RalphConfig) -> list[str]:
    task_doc = _load_task_document(task_progress, config)
    if isinstance(task_doc, TaskJson):
        return task_doc.verify.commands or config.verify_commands
    if isinstance(task_doc, Task):
        return task_doc.get_verify_commands(config.verify_commands)
    return task_progress.verify_commands or config.verify_commands


def _task_visual_verify(task_progress: Any, config: RalphConfig) -> VisualVerifyConfig | None:
    task_doc = _load_task_document(task_progress, config)
    if isinstance(task_doc, TaskJson):
        return task_doc.visual_verify
    if isinstance(task_doc, Task):
        return task_doc.frontmatter.visual_verify
    return None


def _task_for_inspector(task_progress: Any, config: RalphConfig) -> Any:
    task_doc = _load_task_document(task_progress, config)
    if task_doc is not None:
        return task_doc
    return task_progress


@dataclass
class _ResolvedCapability:
    capability_id: str
    capability: AgentCapabilityConfig


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _task_capability_ids(task_doc: Task | TaskJson | None, *, step: str) -> list[str]:
    if not isinstance(task_doc, TaskJson):
        return []
    if step == "code":
        return _dedupe_strings(task_doc.agent_capabilities.code)
    if step == "review":
        return _dedupe_strings(task_doc.agent_capabilities.review)
    raise click.ClickException(f"Unsupported capability step: {step}")


def _resolve_task_capabilities(
    task_doc: Task | TaskJson | None,
    config: RalphConfig,
    *,
    step: str,
) -> list[_ResolvedCapability]:
    capability_ids = _task_capability_ids(task_doc, step=step)
    resolved: list[_ResolvedCapability] = []
    for capability_id in capability_ids:
        try:
            capability = config.get_capability(capability_id)
        except KeyError as exc:
            raise click.ClickException(
                f"Task references undefined capability `{capability_id}` for `{step}`. "
                "Define it in `agent_capabilities` within ralph-config.yaml."
            ) from exc
        resolved.append(_ResolvedCapability(capability_id=capability_id, capability=capability))
    return resolved


def _ensure_capabilities_available(capabilities: list[_ResolvedCapability], config: RalphConfig) -> None:
    workspace_dir = Path(config.workspace_dir)
    for resolved in capabilities:
        check_command = resolved.capability.check_command
        if not check_command:
            continue
        completed = subprocess.run(
            ["bash", "-lc", check_command],
            cwd=str(workspace_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            continue
        details = _tail(f"{completed.stdout}\n{completed.stderr}".strip(), max_chars=1000)
        raise click.ClickException(
            "Capability check failed for "
            f"`{resolved.capability_id}` (command: `{check_command}`).\n{details}"
        )


def _capability_prompt_context(capabilities: list[_ResolvedCapability]) -> list[dict[str, str]]:
    return [
        {
            "id": resolved.capability_id,
            "type": resolved.capability.type,
            "instruction": resolved.capability.instruction,
        }
        for resolved in capabilities
    ]


def _capability_backend_flags(
    capabilities: list[_ResolvedCapability],
    config: RalphConfig,
    *,
    engine: str,
    step: str,
) -> list[str]:
    flags: list[str] = []
    for resolved in capabilities:
        flags.extend(
            config.capability_backend_flags(
                resolved.capability_id,
                engine=engine,
                step=step,
            )
        )
    return flags


def _merge_flags(*flag_groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in flag_groups:
        merged.extend(group)
    return merged


def _validate_task_capability_ids(task_doc: TaskJson, config: RalphConfig) -> list[str]:
    errors: list[str] = []
    for step, ids in (
        ("code", task_doc.agent_capabilities.code),
        ("review", task_doc.agent_capabilities.review),
    ):
        for capability_id in _dedupe_strings(ids):
            if capability_id not in config.agent_capabilities:
                errors.append(
                    f"Task {task_doc.id} references undefined capability "
                    f"`{capability_id}` in `agent_capabilities.{step}`"
                )
    return errors


_DISALLOWED_VERIFY_COMMAND_PATTERNS = (
    r"\bdocker(?:\s+compose)?\s+(?:up|down|start|stop|restart|kill)\b",
    r"\bdocker-compose\s+(?:up|down|start|stop|restart|kill)\b",
    r"\bkubectl\s+(?:apply|delete|rollout|scale|run)\b",
    r"\bsleep\s+\d+\b",
    r"\b(?:npm|pnpm|yarn)\s+(?:dev|start)\b",
    r"\bnext\s+dev\b",
    r"\buvicorn\b",
    r"\bpython(?:3)?\s+-m\s+http\.server\b",
    r"\b(?:pkill|killall|kill)\b",
    r"\bcurl\b[^\n]*\s-X\s+(?:POST|PUT|PATCH|DELETE)\b",
    r"\bwget\b[^\n]*\s+--method=(?:POST|PUT|PATCH|DELETE)\b",
)


def _command_uses_disallowed_verify_behavior(command: str) -> bool:
    lowered = command.lower()
    return any(re.search(pattern, lowered) for pattern in _DISALLOWED_VERIFY_COMMAND_PATTERNS)


def _validate_task_semantics(
    task_doc: TaskJson,
    config: RalphConfig,
    *,
    source_label: str | None = None,
) -> list[str]:
    errors: list[str] = []
    label = source_label or f"Task {task_doc.id}"

    for command in task_doc.verify.commands:
        if _command_uses_disallowed_verify_behavior(command):
            errors.append(
                f"{label}: verify.commands must stay host-side and non-destructive: {command}"
            )

    review_focus = task_doc.review.focus
    review_service_urls = task_doc.review.service_urls
    review_runtime_expectations = task_doc.review.runtime_expectations
    requires_runtime_review = any(
        (review_focus, review_service_urls, review_runtime_expectations)
    )
    if requires_runtime_review and not task_doc.agent_capabilities.review:
        errors.append(
            f"{label}: runtime/service/browser review requires at least one "
            "capability in agent_capabilities.review"
        )

    for url in review_service_urls:
        lowered = url.lower()
        if "localhost" in lowered or "127.0.0.1" in lowered:
            errors.append(
                f"{label}: review.service_urls must use host.docker.internal for "
                f"dockerized runs: {url}"
            )

    if config.review_mode == "unified_agent" and task_doc.visual is not None:
        errors.append(f"{label}: legacy visual block is not allowed in unified_agent loops")

    errors.extend(_validate_task_capability_ids(task_doc, config))
    return errors


def _build_coder_prompt(task_progress: Any, config: RalphConfig) -> str:
    """Render the coder prompt through the Jinja2 template, including retry feedback."""
    task_path = _resolve_task_file_path(str(task_progress.task_file), config)
    if task_path is None:
        return f"# Task\n\n{task_progress.title}"

    try:
        task_doc: Task | TaskJson
        if task_path.suffix.lower() == ".json":
            task_doc = TaskJson.load(task_path)
        else:
            task_doc = Task.load(str(task_path))
    except (ValidationError, Exception):
        # Task file lacks structured frontmatter — fall back to raw content
        return task_path.read_text(encoding="utf-8")

    if isinstance(task_doc, TaskJson):
        task_title = task_doc.title
        task_description = task_doc.description
        acceptance_criteria = task_doc.acceptance_criteria
        files_to_touch = task_doc.files_to_touch
        files_not_to_touch = task_doc.files_not_to_touch
        constraints = task_doc.constraints
        reference_impl = task_doc.reference_impl
    else:
        task_title = task_doc.title
        task_description = task_doc.description
        acceptance_criteria = task_doc.acceptance_criteria
        files_to_touch = task_doc.frontmatter.files_to_touch
        files_not_to_touch = task_doc.frontmatter.files_not_to_touch
        constraints = task_doc.constraints
        reference_impl = task_doc.reference_impl

    capabilities = _resolve_task_capabilities(task_doc, config, step="code")

    is_retry = getattr(task_progress, "retries", 0) > 0
    feedback_entries = getattr(task_progress, "feedback", [])
    attempt_number = getattr(task_progress, "retries", 0) + 1

    project_instructions: str | None = None
    if config.project_instructions:
        pi_path = Path(config.project_instructions)
        if pi_path.exists():
            project_instructions = pi_path.read_text(encoding="utf-8")

    contract_content: str | None = None
    contract_file = getattr(task_progress, "contract_file", None)
    if contract_file:
        c_path = Path(contract_file)
        if c_path.exists():
            contract_content = c_path.read_text(encoding="utf-8")

    template_path = Path(__file__).resolve().parent / "prompts" / "coder.md.j2"
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.render(
        task_title=task_title,
        task_description=task_description,
        acceptance_criteria=acceptance_criteria,
        files_to_touch=files_to_touch,
        files_not_to_touch=files_not_to_touch,
        constraints=constraints,
        reference_impl=reference_impl,
        previous_feedback=format_feedback_for_prompt(filter_feedback_for_coder(feedback_entries)),
        is_retry=is_retry,
        attempt_number=attempt_number,
        max_retries=config.max_retries,
        project_instructions=project_instructions,
        contract_content=contract_content,
        capabilities=capabilities and _capability_prompt_context(capabilities),
    )


def _build_inspector_prompt(task: Any, verification_results: list[dict[str, Any]] | None) -> str:
    acceptance_criteria = getattr(task, "acceptance_criteria", [])
    description_summary = getattr(task, "description", "")
    visual_verify = getattr(task, "visual_verify", None)
    if isinstance(task, TaskJson):
        acceptance_criteria = task.inspect.acceptance_criteria or task.acceptance_criteria
        description_summary = task.inspect.description_summary or task.description
        visual_verify = task.visual_verify

    lines = [
        f"# Inspect Task {task.id}: {task.title}",
        "",
        "## Task Context",
        description_summary,
        "",
        "## Acceptance Criteria",
    ]
    if acceptance_criteria:
        lines.extend([f"{index + 1}. {item}" for index, item in enumerate(acceptance_criteria)])
    else:
        lines.append("1. Validate task completion.")

    lines.extend(
        [
            "",
            "Review task result and return strict JSON:",
            "",
            "Hard rules:",
            "- If any deterministic verification failed, verdict MUST be `fail`.",
        ]
    )
    if visual_verify is not None:
        lines.append("- If visual verification failed or could not run, verdict MUST be `fail`.")
    lines.extend(
        [
            "- Return `pass` only when all acceptance checks are green.",
        ]
    )
    lines.append('{"verdict":"pass|fail","feedback":"..."}')
    if verification_results:
        lines.extend(["", "## Verification results", json.dumps(verification_results, indent=2)])
    return "\n".join(lines) + "\n"


class ReviewerVerdict(BaseModel):
    """Normalized schema for unified reviewer output."""

    verdict: str
    methods_used: list[str]
    feedback: str
    findings: list[str] = Field(default_factory=list)


def _capture_git_diff_since(workspace_dir: str, base_sha: str | None) -> str:
    command = ["git", "--no-pager", "diff", "--binary"]
    if base_sha:
        command.append(base_sha)

    completed = subprocess.run(
        command,
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    diff_text = completed.stdout
    if len(diff_text) <= 120_000:
        return diff_text
    return diff_text[-120_000:]


def _summarize_feedback_sources(sources: list[FeedbackSource]) -> str:
    if not sources:
        return "Task completed successfully."
    chunks: list[str] = []
    for source in sources:
        detail = (source.details or "").strip()
        if detail:
            chunks.append(f"{source.type}: {detail}")
        else:
            chunks.append(f"{source.type}: {source.verdict}")
    summary = " | ".join(chunks)
    if len(summary) <= 500:
        return summary
    return summary[:497] + "..."


def _emit_commit_action(
    *,
    task_id: str,
    task_title: str,
    commit_mode: str,
    progress_file: str,
    latest_feedback_summary: str,
    config: RalphConfig,
) -> dict[str, Any]:
    reviewer_role = _review_backend_role(config)
    reviewer_cfg = config.get_backend(reviewer_role)
    extra_flags = _with_dynamic_codex_reasoning(
        reviewer_cfg,
        step="review",
        task_retries=0,
    )
    return {
        "command": "commit",
        "task_id": task_id,
        "task_title": task_title,
        "commit_mode": commit_mode,
        "progress_file": progress_file,
        "latest_feedback_summary": latest_feedback_summary,
        "image": f"ralph-loop-{reviewer_cfg.engine}",
        "model": reviewer_cfg.model,
        "timeout_seconds": reviewer_cfg.timeout_seconds,
        "extra_flags": extra_flags,
        "auth": config.get_auth(reviewer_cfg.engine).model_dump(),
    }


def _task_review_context(task: Any, config: RalphConfig) -> dict[str, Any]:
    task_doc = _task_for_inspector(task, config)

    if isinstance(task_doc, TaskJson):
        review_acceptance = task_doc.review.acceptance_criteria or task_doc.acceptance_criteria
        review_summary = task_doc.review.description_summary or task_doc.description
        review_focus = task_doc.review.focus
        review_service_urls = task_doc.review.service_urls
        review_runtime_expectations = task_doc.review.runtime_expectations

        if task_doc.visual is not None:
            if task_doc.visual.url and task_doc.visual.url not in review_service_urls:
                review_service_urls = [*review_service_urls, task_doc.visual.url]
            if (
                task_doc.visual.assertion
                and task_doc.visual.assertion not in review_runtime_expectations
            ):
                review_runtime_expectations = [
                    *review_runtime_expectations,
                    task_doc.visual.assertion,
                ]

        if task_doc.inspect is not None:
            if not review_acceptance:
                review_acceptance = task_doc.inspect.acceptance_criteria
            if not review_summary:
                review_summary = task_doc.inspect.description_summary

        return {
            "title": task_doc.title,
            "description": review_summary,
            "acceptance_criteria": review_acceptance,
            "constraints": task_doc.constraints,
            "files_to_touch": task_doc.files_to_touch,
            "files_not_to_touch": task_doc.files_not_to_touch,
            "review_context": {
                "focus": review_focus,
                "service_urls": review_service_urls,
                "runtime_expectations": review_runtime_expectations,
            },
            "task_doc": task_doc,
        }

    if isinstance(task_doc, Task):
        visual_verify = task_doc.frontmatter.visual_verify
        return {
            "title": task_doc.title,
            "description": task_doc.description,
            "acceptance_criteria": task_doc.acceptance_criteria,
            "constraints": task_doc.constraints,
            "files_to_touch": task_doc.frontmatter.files_to_touch,
            "files_not_to_touch": task_doc.frontmatter.files_not_to_touch,
            "review_context": (
                {
                    "focus": [],
                    "service_urls": [visual_verify.url],
                    "runtime_expectations": [visual_verify.assertion],
                }
                if visual_verify is not None
                else {
                    "focus": [],
                    "service_urls": [],
                    "runtime_expectations": [],
                }
            ),
            "task_doc": task_doc,
        }

    return {
        "title": getattr(task, "title", "Task"),
        "description": getattr(task, "description", ""),
        "acceptance_criteria": getattr(task, "acceptance_criteria", []),
        "constraints": [],
        "files_to_touch": [],
        "files_not_to_touch": [],
        "review_context": {
            "focus": [],
            "service_urls": [],
            "runtime_expectations": [],
        },
        "task_doc": None,
    }


def _build_reviewer_prompt(
    task: Any,
    config: RalphConfig,
    iteration: dict[str, Any],
) -> str:
    context = _task_review_context(task, config)
    task_doc = context.get("task_doc")
    review_capabilities = _resolve_task_capabilities(task_doc, config, step="review")
    results = iteration.get("results")
    if not isinstance(results, list):
        results = []

    verify_results = [
        item for item in results if isinstance(item, dict) and str(item.get("step")) == "verify"
    ]
    runtime_results = [
        item
        for item in results
        if isinstance(item, dict) and str(item.get("step", "")).startswith("runtime_")
    ]

    task_base_sha = str(iteration.get("task_base_sha", "")).strip() or None
    git_diff = _capture_git_diff_since(config.workspace_dir, task_base_sha)

    project_instructions = _load_optional_file(config.project_instructions)
    contract_content: str | None = None
    contract_file = getattr(task, "contract_file", None)
    if contract_file:
        contract_content = _load_optional_file(contract_file)

    template_path = Path(__file__).resolve().parent / "prompts" / "reviewer.md.j2"
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.render(
        task_id=getattr(task, "id", ""),
        task_title=context["title"],
        task_description=context["description"],
        acceptance_criteria=context["acceptance_criteria"],
        constraints=context["constraints"],
        files_to_touch=context["files_to_touch"],
        files_not_to_touch=context["files_not_to_touch"],
        task_base_sha=task_base_sha,
        git_diff=git_diff,
        verify_results=verify_results,
        verify_results_json=json.dumps(verify_results, indent=2),
        runtime_results=runtime_results,
        runtime_results_json=json.dumps(runtime_results, indent=2),
        review_context=context["review_context"],
        review_context_json=json.dumps(context["review_context"], indent=2),
        project_instructions=project_instructions,
        contract_content=contract_content,
        capabilities=review_capabilities and _capability_prompt_context(review_capabilities),
    )


def _parse_reviewer_output(stdout: str) -> tuple[str, str]:
    try:
        parsed_raw = _extract_first_json_object(stdout)
        parsed = ReviewerVerdict.model_validate(parsed_raw)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        return "fail", f"Reviewer output could not be parsed as valid JSON schema: {exc}"

    verdict = parsed.verdict.strip().lower()
    if verdict not in {"pass", "fail"}:
        return "fail", f"Reviewer verdict must be pass|fail, got: {parsed.verdict}"
    if not parsed.methods_used:
        return "fail", "Reviewer output missing methods_used evidence"

    findings = "\n".join(f"- {item}" for item in parsed.findings if item.strip())
    details = f"Methods: {', '.join(parsed.methods_used)}\n{parsed.feedback.strip()}"
    if findings:
        details = f"{details}\nFindings:\n{findings}"
    return verdict, details


def _review_backend_role(config: RalphConfig) -> str:
    for role in ("reviewer", "inspect", "inspector"):
        if role in config.backends:
            return role
    raise click.ClickException("No reviewer backend configured (reviewer/inspect/inspector)")


def _runtime_guard_for_phase(config: RalphConfig, phase: str) -> RuntimeGuardConfig | None:
    if phase == "pre_code":
        return config.runtime_guards.pre_code
    if phase == "post_code":
        return config.runtime_guards.post_code
    raise click.ClickException(f"Unknown runtime guard phase: {phase}")


def _runtime_step_name(phase: str) -> str:
    return f"runtime_{phase}"


def _capture_head_sha(workspace_dir: str) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    sha = completed.stdout.strip()
    return sha or None


def _summary(progress: Progress) -> dict[str, int]:
    tasks = [task for phase in progress.phases for task in phase.tasks]
    completed = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
    aborted = sum(1 for task in tasks if task.status == TaskStatus.ABORT)
    return {"completed": completed, "aborted": aborted, "total": len(tasks)}


def _first_aborted_task(progress: Progress) -> Any | None:
    for phase in progress.phases:
        for task in phase.tasks:
            if task.status == TaskStatus.ABORT:
                return task
    return None


def _parse_inspector_output(stdout: str) -> tuple[str, str]:
    try:
        parsed = _extract_first_json_object(stdout)
        if isinstance(parsed, dict):
            verdict = str(parsed.get("verdict", "fail")).strip().lower()
            feedback = str(parsed.get("feedback", "")).strip()
            if verdict in {"pass", "fail"}:
                return verdict, feedback
    except (ValueError, json.JSONDecodeError):
        pass
    return "fail", stdout.strip() or "Inspector output could not be parsed as JSON"


def _collect_iteration_results(result_dir: Path, iteration: dict[str, Any]) -> list[dict[str, Any]]:
    if "results" in iteration and isinstance(iteration["results"], list):
        return [item for item in iteration["results"] if isinstance(item, dict)]

    step_result_file = result_dir / "step-result.json"
    if not step_result_file.exists():
        return []

    payload = json.loads(step_result_file.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [payload]
    return []


def _append_step_result(iteration: dict[str, Any], step_result: dict[str, Any]) -> None:
    results = iteration.get("results")
    if not isinstance(results, list):
        results = []
        iteration["results"] = results
    results.append(step_result)


def _collect_verification_results(iteration: dict[str, Any]) -> list[dict[str, Any]]:
    results = iteration.get("results")
    if not isinstance(results, list):
        return []
    return [
        item
        for item in results
        if isinstance(item, dict) and str(item.get("step", "")) in {"verify", "visual"}
    ]


def _visual_backend_role(config: RalphConfig) -> str:
    return "visual" if "visual" in config.backends else "inspector"


def _emit_code_action(
    *,
    task: Any,
    config: RalphConfig,
    paths: _Paths,
) -> dict[str, Any]:
    coder = config.get_backend("coder")
    task_doc = _load_task_document(task, config)
    code_capabilities = _resolve_task_capabilities(task_doc, config, step="code")
    _ensure_capabilities_available(code_capabilities, config)
    capability_flags = _capability_backend_flags(
        code_capabilities,
        config,
        engine=coder.engine,
        step="code",
    )
    coder_flags = _merge_flags(
        _with_dynamic_codex_reasoning(
            coder,
            step="code",
            task_retries=getattr(task, "retries", 0),
        ),
        capability_flags,
    )
    return {
        "command": "code",
        "task_id": task.id,
        "image": f"ralph-loop-{coder.engine}",
        "prompt_file": str(paths.coder_prompt_path),
        "model": coder.model,
        "timeout_seconds": coder.timeout_seconds,
        "extra_flags": coder_flags,
        "capabilities": [item.capability_id for item in code_capabilities],
        "auth": config.get_auth(coder.engine).model_dump(),
    }


def _emit_runtime_action(
    *,
    task: Any,
    config: RalphConfig,
    phase: str,
) -> dict[str, Any] | None:
    guard = _runtime_guard_for_phase(config, phase)
    if guard is None:
        return None
    return {
        "command": "runtime",
        "task_id": task.id,
        "phase": phase,
        "runtime_command": guard.command,
        "timeout_seconds": guard.timeout_seconds,
        "on_failure": guard.on_failure,
        "workspace_dir": config.workspace_dir,
    }


def _emit_review_action(
    *,
    task: Any,
    config: RalphConfig,
    paths: _Paths,
    iteration: dict[str, Any],
) -> dict[str, Any]:
    task_doc = _task_for_inspector(task, config)
    review_capabilities = _resolve_task_capabilities(task_doc, config, step="review")
    _ensure_capabilities_available(review_capabilities, config)

    reviewer_prompt = _build_reviewer_prompt(task, config, iteration)
    paths.reviewer_prompt_path.write_text(reviewer_prompt, encoding="utf-8")

    reviewer_role = _review_backend_role(config)
    reviewer_cfg = config.get_backend(reviewer_role)
    capability_flags = _capability_backend_flags(
        review_capabilities,
        config,
        engine=reviewer_cfg.engine,
        step="review",
    )
    reviewer_flags = _merge_flags(
        _with_dynamic_codex_reasoning(
            reviewer_cfg,
            step="review",
            task_retries=getattr(task, "retries", 0),
        ),
        capability_flags,
    )
    return {
        "command": "review",
        "task_id": task.id,
        "image": f"ralph-loop-{reviewer_cfg.engine}",
        "prompt_file": str(paths.reviewer_prompt_path),
        "model": reviewer_cfg.model,
        "timeout_seconds": reviewer_cfg.timeout_seconds,
        "extra_flags": reviewer_flags,
        "capabilities": [item.capability_id for item in review_capabilities],
        "auth": config.get_auth(reviewer_cfg.engine).model_dump(),
    }


def _with_dynamic_codex_reasoning(
    backend: BackendConfig,
    *,
    step: str,
    task_retries: int,
) -> list[str]:
    """Return backend flags with dynamic Codex reasoning effort per step and retry count."""
    effective_flags = _strip_codex_reasoning_override(backend.extra_flags)
    if backend.engine != "codex":
        return effective_flags

    reasoning_effort = "xhigh" if step == "code" and task_retries > 0 else "high"
    effective_flags.append(f'--config=model_reasoning_effort="{reasoning_effort}"')
    return effective_flags


def _strip_codex_reasoning_override(extra_flags: list[str]) -> list[str]:
    """Drop user-defined model_reasoning_effort overrides from extra flags."""
    cleaned: list[str] = []
    index = 0
    while index < len(extra_flags):
        flag = extra_flags[index]
        if flag in {"-c", "--config"}:
            if index + 1 < len(extra_flags):
                candidate = extra_flags[index + 1].strip()
                if candidate.startswith("model_reasoning_effort="):
                    index += 2
                    continue
            cleaned.append(flag)
            index += 1
            continue

        if flag.startswith("--config="):
            candidate = flag.split("=", 1)[1].strip()
            if candidate.startswith("model_reasoning_effort="):
                index += 1
                continue

        cleaned.append(flag)
        index += 1

    return cleaned


def _select_available_backend() -> tuple[str, Any]:
    for engine in ("codex", "copilot", "claude"):
        backend = get_backend(engine)
        if backend.is_available():
            return engine, backend
    raise click.ClickException("No supported AI CLI found in PATH (codex/copilot/claude)")


def _run_prompt_with_available_backend(
    prompt_file: Path,
    *,
    model: str | None = None,
    timeout_seconds: int = 600,
    extra_flags: list[str] | None = None,
) -> int:
    if not prompt_file.exists():
        raise click.ClickException(f"Prompt file not found: {prompt_file}")

    prompt_content = prompt_file.read_text(encoding="utf-8")
    _, backend = _select_available_backend()
    backend_cwd = _infer_prompt_cwd(prompt_file)
    result = backend.execute(
        prompt=prompt_content,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags or [],
        cwd=str(backend_cwd),
    )
    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    output_streamed = bool(getattr(result, "output_streamed", False))
    exit_code = int(getattr(result, "exit_code", 1))

    if stdout and not output_streamed:
        click.echo(stdout, nl=False)
    if stderr and not output_streamed:
        click.echo(stderr, err=True, nl=False)

    return exit_code


def _infer_prompt_cwd(prompt_file: Path) -> Path:
    prompt_parent = prompt_file.parent
    if prompt_parent.name == ".ralph-tmp":
        return prompt_parent.parent
    return prompt_parent


def _invoke_self(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ralph_loop", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _invoke_docker_cli(
    action: dict[str, Any], prompt_file: Path, workspace: Path, subcommand: str
) -> subprocess.CompletedProcess[str]:
    image = action.get("image")
    if not isinstance(image, str) or not image:
        raise click.ClickException("Missing image in next-action payload for docker sandbox")

    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workspace}:/workspace",
    ]

    auth = action.get("auth")
    if isinstance(auth, dict):
        env_values = auth.get("env")
        if isinstance(env_values, list):
            for env_name in env_values:
                if not isinstance(env_name, str):
                    continue
                env_value = os.environ.get(env_name)
                if env_value is None:
                    continue
                command.extend(["-e", f"{env_name}={env_value}"])

        mount_values = auth.get("mount")
        if isinstance(mount_values, list):
            for mount_entry in mount_values:
                if not isinstance(mount_entry, dict):
                    continue
                source_value = mount_entry.get("source")
                target_value = mount_entry.get("target")
                if not isinstance(source_value, str) or not isinstance(target_value, str):
                    continue
                expanded = Path(source_value).expanduser()
                if not expanded.exists():
                    continue
                if target_value.startswith("~/"):
                    target_value = str(Path("/home/ralph") / target_value[2:])
                container_target = Path(target_value)
                command.extend(["-v", f"{expanded}:{container_target}"])

    container_prompt = Path("/workspace") / prompt_file.relative_to(workspace)
    command.extend([image, subcommand, "--prompt-file", str(container_prompt)])

    return subprocess.run(command, capture_output=True, text=True, check=False)


def _write_step_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _tail(output: str, max_chars: int = 4000) -> str:
    if len(output) <= max_chars:
        return output
    return output[-max_chars:]


def _phase_status_label(status: PhaseStatus) -> str:
    labels = {
        PhaseStatus.NOT_STARTED: "NOT STARTED",
        PhaseStatus.IN_PROGRESS: "IN PROGRESS",
        PhaseStatus.COMPLETED: "COMPLETED",
    }
    return labels[status]


def _task_label(task_status: TaskStatus, retries: int, max_retries: int) -> str:
    if task_status == TaskStatus.COMPLETED:
        return "✅"
    if task_status == TaskStatus.IN_PROGRESS:
        return "🔄"
    if task_status == TaskStatus.FAILED:
        return "❌"
    if task_status == TaskStatus.ABORT:
        return "⛔"
    if retries > 0:
        return f"🔁({retries}/{max_retries})"
    return "⬜"


def _slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized.strip("-") or "task"


def _derive_loop_dir_from_source(source: Path) -> Path:
    return source.parent / ".ralph-loop"


def _normalized_backend_for_reviewer(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    engine = raw.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        return None

    normalized: dict[str, Any] = {"engine": engine}
    model = raw.get("model")
    if isinstance(model, str) and model.strip():
        normalized["model"] = model

    timeout_seconds = raw.get("timeout_seconds")
    if isinstance(timeout_seconds, int) and timeout_seconds > 0:
        normalized["timeout_seconds"] = timeout_seconds
    else:
        normalized["timeout_seconds"] = 300

    extra_flags = raw.get("extra_flags")
    if isinstance(extra_flags, list):
        normalized["extra_flags"] = extra_flags

    return normalized


def _default_reviewer_backend(backends: dict[str, Any]) -> dict[str, Any]:
    for role in ("inspector", "inspect", "coder"):
        candidate = _normalized_backend_for_reviewer(backends.get(role))
        if candidate is not None:
            return candidate
    return {
        "engine": "codex",
        "model": "gpt-5.4-codex",
        "timeout_seconds": 300,
        "extra_flags": [],
    }


def _normalize_runtime_guard(
    raw: Any,
    *,
    phase_name: str,
    default_on_failure: str,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw, dict):
        raise click.ClickException(
            "config.runtime_guards."
            f"{phase_name} must be defined explicitly; init will not create runtime guard defaults"
        )

    changed = False
    guard = dict(raw)
    command = guard.get("command")
    if not isinstance(command, str) or not command.strip():
        raise click.ClickException(
            f"config.runtime_guards.{phase_name}.command must be defined explicitly"
        )

    timeout_seconds = guard.get("timeout_seconds")
    if not isinstance(timeout_seconds, int) or timeout_seconds < 1:
        guard["timeout_seconds"] = 180
        changed = True

    on_failure = guard.get("on_failure")
    if on_failure not in {"pause_loop", "abort_loop", "fail_attempt"}:
        guard["on_failure"] = default_on_failure
        changed = True

    return guard, changed


def _ensure_config(config_path: Path, *, loop_dir: Path) -> RalphConfig:
    raw_payload: dict[str, Any] = {}
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            raw_payload = loaded
    else:
        raw_payload = {
            "max_retries": 3,
            "backends": {
                "coder": {"engine": "codex", "model": "gpt-5.3-codex", "timeout_seconds": 600},
                "inspector": {
                    "engine": "copilot",
                    "model": "claude-opus-4-6",
                    "timeout_seconds": 300,
                },
            },
            "verify_commands": [],
            "auth": {},
        }

    changed = False
    if raw_payload.get("review_mode") != "unified_agent":
        raw_payload["review_mode"] = "unified_agent"
        changed = True

    backends = raw_payload.get("backends")
    if not isinstance(backends, dict):
        backends = {}
        raw_payload["backends"] = backends
        changed = True

    init_backend = _normalized_backend_for_reviewer(backends.get("init"))
    if init_backend is None:
        init_backend = _normalized_backend_for_reviewer(backends.get("initialize"))
    if init_backend is None:
        init_backend = _normalized_backend_for_reviewer(backends.get("coder"))
    if init_backend is None:
        init_backend = {
            "engine": "codex",
            "model": "gpt-5.4-codex",
            "timeout_seconds": 600,
            "extra_flags": [],
        }
    if backends.get("init") != init_backend:
        backends["init"] = init_backend
        changed = True

    reviewer = _normalized_backend_for_reviewer(backends.get("reviewer"))
    if reviewer is None:
        backends["reviewer"] = _default_reviewer_backend(backends)
        changed = True

    for stale_role in ("initialize", "inspector", "inspect", "visual"):
        if stale_role in backends:
            backends.pop(stale_role, None)
            changed = True

    runtime_guards = raw_payload.get("runtime_guards")
    if not isinstance(runtime_guards, dict):
        raise click.ClickException(
            "config.runtime_guards must be defined explicitly; init will not create runtime guard defaults"
        )

    pre_code, pre_changed = _normalize_runtime_guard(
        runtime_guards.get("pre_code"),
        phase_name="pre_code",
        default_on_failure="pause_loop",
    )
    runtime_guards["pre_code"] = pre_code
    changed = changed or pre_changed

    post_code, post_changed = _normalize_runtime_guard(
        runtime_guards.get("post_code"),
        phase_name="post_code",
        default_on_failure="fail_attempt",
    )
    runtime_guards["post_code"] = post_code
    changed = changed or post_changed

    if "verify_commands" not in raw_payload or not isinstance(raw_payload.get("verify_commands"), list):
        raw_payload["verify_commands"] = []
        changed = True

    if changed or not config_path.exists():
        click.echo(f"[init] Updating loop config defaults: {config_path}")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(raw_payload, sort_keys=False), encoding="utf-8")

    config = RalphConfig.load(str(config_path), loop_dir=str(loop_dir))
    return config


def _load_plan_template() -> Template:
    template_path = Path(__file__).resolve().parent / "prompts" / "plan_to_tasks.md.j2"
    return Template(template_path.read_text(encoding="utf-8"))


def _extract_first_json_object(payload: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(payload):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(payload[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("No JSON object found in backend output")


def _fallback_plan(source_text: str, title_hint: str) -> GeneratedPlan:
    checklist: list[str] = []
    bullets: list[str] = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("- [ ]"):
            candidate = re.sub(r"^- \[ \]\s*", "", stripped).strip()
            if candidate:
                checklist.append(candidate)
            continue

        if stripped.startswith(("- ", "* ")):
            candidate = stripped[2:].strip()
            if candidate:
                bullets.append(candidate)

    candidates = checklist if checklist else bullets
    tasks: list[GeneratedTask] = []
    for candidate in candidates:
        task_id = f"{len(tasks) + 1:02d}"
        tasks.append(
            GeneratedTask(
                id=task_id,
                title=candidate,
                description=candidate,
                acceptance_criteria=["Implementation is complete and validated."],
                test_plan="1. Run the configured verification commands.",
            )
        )

    if not tasks:
        summary = source_text.strip().splitlines()
        first_line = summary[0] if summary else title_hint
        tasks.append(
            GeneratedTask(
                id="01",
                title=first_line[:80],
                description=source_text.strip() or title_hint,
                acceptance_criteria=["Implementation is complete and validated."],
                test_plan="1. Run the configured verification commands.",
            )
        )

    plan = GeneratedPlan(
        title=title_hint, phases=[GeneratedPhase(id=1, name="Phase 1", tasks=tasks)]
    )
    return _apply_plan_hints(plan, source_text)


def _contains_runtime_review_instruction(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "visual verification",
        "visual check",
        "visually",
        "visualmente",
        "revisar visual",
        "browser review",
        "runtime review",
        "service review",
    )
    return any(marker in lowered for marker in markers)


def _dockerize_local_service_url(url: str) -> str:
    normalized = url.strip()
    replacements = {
        "http://localhost:": "http://host.docker.internal:",
        "http://127.0.0.1:": "http://host.docker.internal:",
        "https://localhost:": "https://host.docker.internal:",
        "https://127.0.0.1:": "https://host.docker.internal:",
    }
    for source, target in replacements.items():
        if normalized.lower().startswith(source):
            return target + normalized[len(source) :]
    return normalized


def _extract_review_context_hint(source_text: str) -> dict[str, list[str]] | None:
    lowered = source_text.lower()
    if not _contains_runtime_review_instruction(source_text):
        return None

    assertion_match = re.search(
        r"(?:Assertion|Aserción):\s*(.+)",
        source_text,
        flags=re.IGNORECASE,
    )
    url_matches = re.findall(
        r"https?://[^\s`\"')]+",
        source_text,
        flags=re.IGNORECASE,
    )
    service_urls = _dedupe_strings([_dockerize_local_service_url(url) for url in url_matches])
    runtime_expectations: list[str] = []
    focus: list[str] = []

    if assertion_match:
        runtime_expectations.append(assertion_match.group(1).strip())

    review_phrase = _extract_visual_focus_terms(lowered)
    if review_phrase:
        focus.extend(f"Review runtime/UI behavior related to: {term}" for term in review_phrase)

    if not focus and service_urls:
        focus.append("Review externally observable runtime or UI behavior using the provided service URLs.")

    if not any((focus, service_urls, runtime_expectations)):
        return None

    return {
        "focus": focus,
        "service_urls": service_urls,
        "runtime_expectations": runtime_expectations,
    }


def _merge_generated_review_context(
    task: GeneratedTask,
    review_hint: dict[str, list[str]],
) -> None:
    if not isinstance(task.review, dict):
        task.review = {}

    for key in ("focus", "service_urls", "runtime_expectations"):
        existing = task.review.get(key)
        existing_values = (
            [item for item in existing if isinstance(item, str) and item.strip()]
            if isinstance(existing, list)
            else []
        )
        incoming_values = review_hint.get(key, [])
        task.review[key] = _dedupe_strings([*existing_values, *incoming_values])


def _apply_plan_hints(plan: GeneratedPlan, source_text: str) -> GeneratedPlan:
    review_hint = _extract_review_context_hint(source_text)

    directive_text = source_text.lower()
    review_only_at_end = _is_final_only_visual_directive(directive_text)
    review_focus_terms = _extract_visual_focus_terms(directive_text)

    has_review_context = False
    flat_tasks: list[GeneratedTask] = []
    for phase in plan.phases:
        for task in phase.tasks:
            flat_tasks.append(task)
            if isinstance(task.review, dict) and any(
                isinstance(task.review.get(key), list) and task.review.get(key)
                for key in ("focus", "service_urls", "runtime_expectations")
            ):
                has_review_context = True

    if review_hint is not None and flat_tasks:
        if review_only_at_end:
            target = _select_visual_target(flat_tasks, review_focus_terms, prefer_last=True)
            if target is None:
                target = flat_tasks[-1]
            for task in flat_tasks:
                task.review = {}
            _merge_generated_review_context(target, review_hint)
            return plan

        if review_focus_terms:
            target = _select_visual_target(flat_tasks, review_focus_terms, prefer_last=False)
            if target is not None:
                for task in flat_tasks:
                    if task is not target:
                        task.review = {}
                _merge_generated_review_context(target, review_hint)
                return plan

    if review_hint is not None and not has_review_context:
        keywords = ("visual", "ui", "web", "index.html", "homepage", "page")
        fallback_task: GeneratedTask | None = None
        for phase in plan.phases:
            for task in phase.tasks:
                if fallback_task is None:
                    fallback_task = task
                haystack = f"{task.title} {task.description}".lower()
                if any(keyword in haystack for keyword in keywords):
                    _merge_generated_review_context(task, review_hint)
                    return plan
        if fallback_task is not None:
            _merge_generated_review_context(fallback_task, review_hint)

    return plan


def _is_final_only_visual_directive(text: str) -> bool:
    patterns = (
        r"solo\s+al\s+final[^\n]*visual",
        r"only\s+at\s+the\s+end[^\n]*visual",
        r"visual[^\n]*only\s+at\s+the\s+end",
        r"only\s+final[^\n]*visual",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _extract_visual_focus_terms(text: str) -> list[str]:
    patterns = (
        r"(?:revisar|verificar|validar)(?:\s+solo\s+al\s+final)?\s+visualmente\s+que\s+([^\n\.;]+)",
        r"(?:visually\s+(?:review|verify|check))(?:\s+that)?\s+([^\n\.;]+)",
    )

    phrase: str | None = None
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            phrase = match.group(1).strip()
            break

    if phrase is None:
        return []

    stop_words = {
        "que",
        "the",
        "and",
        "con",
        "los",
        "las",
        "para",
        "from",
        "with",
        "all",
        "todos",
        "todas",
        "solo",
        "final",
        "esten",
        "estén",
        "sean",
    }
    terms: list[str] = []
    for token in re.split(r"\W+", phrase):
        normalized = token.strip().lower()
        if len(normalized) < 3 or normalized in stop_words:
            continue
        if normalized not in terms:
            terms.append(normalized)
    return terms


def _select_visual_target(
    tasks: list[GeneratedTask],
    focus_terms: list[str],
    *,
    prefer_last: bool,
) -> GeneratedTask | None:
    if not tasks:
        return None

    if focus_terms:
        scored_matches: list[tuple[int, GeneratedTask]] = []
        for task in tasks:
            haystack = f"{task.title} {task.description}".lower()
            score = sum(1 for term in focus_terms if term in haystack)
            if score > 0:
                scored_matches.append((score, task))
        if scored_matches:
            max_score = max(score for score, _task in scored_matches)
            best_tasks = [task for score, task in scored_matches if score == max_score]
            return best_tasks[-1] if prefer_last else best_tasks[0]

    keywords = ("visual", "ui", "web", "index.html", "homepage", "page")
    keyword_matches = [
        task
        for task in tasks
        if any(keyword in f"{task.title} {task.description}".lower() for keyword in keywords)
    ]
    if keyword_matches:
        return keyword_matches[-1] if prefer_last else keyword_matches[0]

    return tasks[-1] if prefer_last else tasks[0]


def _build_prompt(
    source_content: str,
    config: RalphConfig,
    *,
    plan_output_path: Path,
    plan_validation_command: str,
    user_directives: str = "",
) -> str:
    project_instructions = ""
    if config.project_instructions:
        instructions_path = Path(config.project_instructions)
        if instructions_path.exists():
            project_instructions = instructions_path.read_text(encoding="utf-8")

    available_capabilities: list[dict[str, Any]] = []
    for capability_id, capability in config.agent_capabilities.items():
        supports_code = any(flags.code for flags in capability.backend_flags.values())
        supports_review = any(flags.review for flags in capability.backend_flags.values())
        if not capability.backend_flags:
            supports_code = True
            supports_review = True
        available_capabilities.append(
            {
                "id": capability_id,
                "type": capability.type,
                "instruction": capability.instruction,
                "supports_code": supports_code,
                "supports_review": supports_review,
            }
        )

    template = _load_plan_template()
    return template.render(
        source_content=source_content,
        project_instructions=project_instructions,
        user_directives=user_directives,
        available_capabilities=available_capabilities,
        plan_output_path=str(plan_output_path),
        plan_validation_command=plan_validation_command,
    )


def _load_user_directives(instructions: str | None, instructions_file: Path | None) -> str:
    chunks: list[str] = []

    if instructions_file is not None:
        file_content = instructions_file.read_text(encoding="utf-8").strip()
        if file_content:
            chunks.append(file_content)

    if instructions:
        inline_content = instructions.strip()
        if inline_content:
            chunks.append(inline_content)

    return "\n\n".join(chunks)


def _ensure_init_directories(config: RalphConfig) -> None:
    directories = [
        Path(config.workspace_dir),
        Path(config.task_dir),
        Path(config.progress_file).parent,
        Path(config.pause_file).parent,
    ]
    seen: set[Path] = set()
    for directory in directories:
        resolved = directory.resolve()
        if resolved in seen:
            continue
        if not resolved.exists():
            click.echo(f"[init] Creating directory: {resolved}")
        resolved.mkdir(parents=True, exist_ok=True)
        seen.add(resolved)


def _select_init_backend(config: RalphConfig) -> BackendConfig:
    if "init" in config.backends:
        return config.get_backend("init")
    if "initialize" in config.backends:
        return config.get_backend("initialize")
    if "inspector" in config.backends:
        return config.get_backend("inspector")
    return config.get_backend("coder")


def _generate_plan_with_backend(
    *,
    config: RalphConfig,
    prompt: str,
    plan_output_path: Path,
    backend_override: str | None,
    model_override: str | None,
    validation_feedback: str | None = None,
) -> tuple[GeneratedPlan | None, str | None, ExecutionResult | None, str]:
    role_backend = _select_init_backend(config)
    engine = backend_override or role_backend.engine
    model = model_override or role_backend.model

    backend = get_backend(engine)
    if not backend.is_available():
        return None, f"Backend '{engine}' is not available", None, prompt

    prompt_with_feedback = prompt
    if validation_feedback:
        prompt_with_feedback = (
            f"{prompt}\n\n"
            "## Validation Errors\n"
            f"{validation_feedback.strip()}\n\n"
            "Fix these errors, rewrite the complete generated plan file, and rerun the "
            "validation command."
        )

    try:
        plan_output_path.parent.mkdir(parents=True, exist_ok=True)
        plan_output_path.write_text("", encoding="utf-8")
    except OSError:
        return (
            None,
            f"Could not reset generated plan output file: {plan_output_path}",
            None,
            prompt_with_feedback,
        )

    try:
        result = backend.execute(
            prompt=prompt_with_feedback,
            model=model,
            timeout_seconds=role_backend.timeout_seconds,
            extra_flags=role_backend.extra_flags,
            cwd=config.workspace_dir,
        )
    except OSError:
        return None, f"Backend '{engine}' failed to start", None, prompt_with_feedback

    if result.exit_code != 0:
        details = result.stderr.strip() or f"Backend exited with code {result.exit_code}"
        return None, details, result, prompt_with_feedback

    try:
        generated_plan = _load_generated_plan_from_file(plan_output_path)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        return None, f"Generated plan file is invalid: {exc}", result, prompt_with_feedback

    return generated_plan, None, result, prompt_with_feedback


def _truncate_for_log(text: str, max_chars: int = 1200) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


def _write_init_attempt_artifacts(
    *,
    tmp_dir: Path,
    attempt: int,
    prompt_text: str,
    result: ExecutionResult | None,
) -> tuple[Path, Path | None, Path | None]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = tmp_dir / f"init-attempt-{attempt:02d}-prompt.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")

    if result is None:
        return prompt_path, None, None

    stdout_path = tmp_dir / f"init-attempt-{attempt:02d}-stdout.txt"
    stderr_path = tmp_dir / f"init-attempt-{attempt:02d}-stderr.txt"
    stdout_text = str(getattr(result, "stdout", "") or "")
    stderr_text = str(getattr(result, "stderr", "") or "")
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    return prompt_path, stdout_path, stderr_path


def _validate_generated_plan_tasks(plan: GeneratedPlan) -> list[str]:
    errors: list[str] = []
    task_counter = 0
    for phase_index, phase in enumerate(plan.phases, start=1):
        for task in phase.tasks:
            task_counter += 1
            task_id = f"{task_counter:02d}"
            try:
                task.to_task_json(phase_id=phase_index, task_id=task_id)
            except ValidationError as exc:
                errors.append(f"Task {task_id}: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Task {task_id}: {exc}")
    return errors


def _write_task_json(task_dir: Path, phase_id: int, task: GeneratedTask, task_id: str) -> Path:
    slug = _slugify(task.title)
    task_filename = f"{task_id}-{slug}.json"
    task_path = task_dir / task_filename
    task_json = task.to_task_json(phase_id=phase_id, task_id=task_id)
    task_json.save(task_path)
    return task_path


def _write_generated_artifacts(ctx: _InitContext, plan: GeneratedPlan) -> tuple[int, Path, Path]:
    task_dir = Path(ctx.config.task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)

    phase_payloads: list[dict[str, Any]] = []
    task_counter = 0

    for phase_index, phase in enumerate(plan.phases, start=1):
        phase_tasks: list[dict[str, Any]] = []
        for task in phase.tasks:
            task_counter += 1
            task_id = f"{task_counter:02d}"
            task_path = _write_task_json(task_dir, phase_index, task, task_id)
            task_json = TaskJson.load(task_path)

            phase_tasks.append(
                {
                    "id": task_id,
                    "title": task.title,
                    "task_file": str(task_path.resolve()),
                    "contract_file": None,
                    "status": "not_started",
                    "retries": 0,
                    "verify_commands": task_json.verify.commands,
                    "feedback": [],
                }
            )

        phase_payloads.append(
            {
                "id": phase_index,
                "name": phase.name,
                "status": "not_started",
                "tasks": phase_tasks,
            }
        )

    progress_payload = {
        "meta": {"title": plan.title, "started": "2026-03-01", "current_phase": 1},
        "phases": phase_payloads,
    }
    progress_path = Path(ctx.config.progress_file)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(yaml.safe_dump(progress_payload, sort_keys=False), encoding="utf-8")

    return task_counter, task_dir, progress_path


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _collect_validate_errors(config: RalphConfig, progress: Progress) -> list[str]:
    task_dir = Path(config.task_dir)
    progress_task_files: set[Path] = set()
    missing_in_progress: list[str] = []

    for phase in progress.phases:
        for task in phase.tasks:
            resolved = _resolve_task_file_path(task.task_file, config)
            if resolved is None:
                missing_in_progress.append(task.task_file)
                continue
            progress_task_files.add(resolved)

    if not task_dir.exists():
        return [f"Task directory does not exist: {task_dir}"]

    task_files_on_disk = {
        path.resolve() for pattern in ("*.md", "*.json") for path in task_dir.glob(pattern)
    }
    if missing_in_progress:
        return [f"Task files referenced in progress are missing: {missing_in_progress}"]

    validation_errors: list[str] = []
    for task_path in sorted(progress_task_files):
        if task_path.suffix.lower() != ".json":
            continue
        json_errors = validate_task_file(task_path)
        validation_errors.extend(json_errors)
        if json_errors:
            continue
        try:
            task_doc = TaskJson.load(task_path)
        except Exception as exc:  # noqa: BLE001
            validation_errors.append(f"Task schema error in {task_path}: {exc}")
            continue
        validation_errors.extend(_validate_task_semantics(task_doc, config, source_label=str(task_path)))

    not_referenced = [
        path for path in sorted(task_files_on_disk) if path not in progress_task_files
    ]
    if not_referenced:
        validation_errors.append(f"Task files not referenced in progress: {not_referenced}")

    known_engines = {"codex", "copilot", "claude"}
    unknown = sorted({backend.engine for backend in config.backends.values()} - known_engines)
    if unknown:
        validation_errors.append(f"Unknown backend engines: {unknown}")

    for engine in {backend.engine for backend in config.backends.values()}:
        _ = config.get_auth(engine)

    return validation_errors


def _collect_loop_check_errors(
    *,
    config_path: Path,
    loop_dir: Path,
    config: RalphConfig,
    progress: Progress,
) -> list[str]:
    raw_config = _load_yaml_mapping(config_path)
    errors: list[str] = []

    if not config_path.exists():
        errors.append(f"config file not found: {config_path}")
    if not Path(config.progress_file).exists():
        errors.append(f"missing PROGRESS.yaml: {config.progress_file}")
    if not Path(config.task_dir).exists():
        errors.append(f"missing task dir: {config.task_dir}")
    elif not any(Path(config.task_dir).glob("*.json")):
        errors.append(f"tasks directory has no JSON task files: {config.task_dir}")

    if config.review_mode == "unified_agent":
        if raw_config.get("verify_commands") != []:
            errors.append(
                "config.verify_commands should be [] for unified_agent loops; "
                "keep service availability/health in runtime guards and add task "
                "verify_commands only when explicitly justified"
            )

        backends = raw_config.get("backends")
        if not isinstance(backends, dict):
            errors.append("config.backends missing or invalid")
        else:
            missing_backends = [name for name in ("init", "coder", "reviewer") if name not in backends]
            if missing_backends:
                errors.append(
                    "config.backends missing required entries for unified_agent: "
                    + ", ".join(missing_backends)
                )
            extra_backends = sorted(set(backends.keys()) - {"init", "coder", "reviewer"})
            if extra_backends:
                errors.append(
                    "config.backends contains deprecated/unsupported entries for unified_agent: "
                    + ", ".join(extra_backends)
                )

        runtime_guards = raw_config.get("runtime_guards")
        if not isinstance(runtime_guards, dict):
            errors.append("config.runtime_guards missing or invalid")
        else:
            for phase_name in ("pre_code", "post_code"):
                phase_cfg = runtime_guards.get(phase_name)
                if not isinstance(phase_cfg, dict):
                    errors.append(f"config.runtime_guards.{phase_name} missing or invalid")
                    continue
                command = phase_cfg.get("command")
                if not isinstance(command, str) or not command.strip():
                    errors.append(f"config.runtime_guards.{phase_name}.command must be non-empty")

        guard_path = loop_dir / "guard.sh"
        runtime_guard_commands = [
            str(runtime_guard.get("command", ""))
            for runtime_guard in (raw_config.get("runtime_guards") or {}).values()
            if isinstance(raw_config.get("runtime_guards"), dict) and isinstance(runtime_guard, dict)
        ]
        for command in runtime_guard_commands:
            if "../guard.sh" in command:
                errors.append(
                    "runtime guard commands must be resolvable from workspace root; "
                    "do not use '../guard.sh'"
                )
        loop_local_guard_referenced = any(
            "../guard.sh" in command
            or re.search(r"(^|[\"'\s])(?:\./)?guard\.sh(?:[\s\"']|$)", command)
            for command in runtime_guard_commands
        )
        if loop_local_guard_referenced and (not guard_path.exists() or not os.access(guard_path, os.X_OK)):
            errors.append(f"loop-local guard script must exist and be executable: {guard_path}")

    docker_run_args = raw_config.get("docker_run_args")
    contexts = {}
    if docker_run_args is not None:
        if not isinstance(docker_run_args, dict):
            errors.append("config.docker_run_args must be a mapping when present")
        else:
            contexts = docker_run_args.get("contexts", {})
            if contexts and not isinstance(contexts, dict):
                errors.append("config.docker_run_args.contexts must be a mapping")
                contexts = {}

    flow_runtime = raw_config.get("flow_runtime")
    if isinstance(flow_runtime, dict):
        reference_codebases = flow_runtime.get("reference_codebases", [])
        if reference_codebases and not isinstance(reference_codebases, list):
            errors.append("config.flow_runtime.reference_codebases must be a list")
        elif isinstance(reference_codebases, list):
            for reference in reference_codebases:
                if not isinstance(reference, dict):
                    errors.append("reference_codebase entries must be mappings")
                    continue
                reference_id = str(reference.get("id", "")).strip()
                host_path = str(reference.get("host_path", "")).strip()
                container_path = str(reference.get("container_path", "")).strip()
                mode = str(reference.get("mode", "")).strip()
                if not reference_id or not host_path or not container_path:
                    errors.append(f"reference_codebase is incomplete: {reference}")
                    continue
                if mode != "ro":
                    errors.append(
                        f"reference_codebase.mode must be 'ro' for read-only access: {reference_id}"
                    )
                mount_value = f"{host_path}:{container_path}:ro"
                for context_name in ("init", "code", "review"):
                    values = contexts.get(context_name, [])
                    if not isinstance(values, list) or mount_value not in values:
                        errors.append(
                            "config.docker_run_args.contexts."
                            f"{context_name} must mount read-only reference '{mount_value}'"
                        )

    agent_capabilities = raw_config.get("agent_capabilities")
    if agent_capabilities is not None and not isinstance(agent_capabilities, dict):
        errors.append("config.agent_capabilities must be a mapping when present")

    for phase in progress.phases:
        for task in phase.tasks:
            task_path = _resolve_task_file_path(task.task_file, config)
            if task_path is None or task_path.suffix.lower() != ".json":
                continue
            try:
                task_doc = TaskJson.load(task_path)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"invalid JSON task file: {task_path} ({exc})")
                continue
            errors.extend(_validate_task_semantics(task_doc, config, source_label=str(task_path)))

    return errors


def _validate_generated_plan_semantics(plan: GeneratedPlan, config: RalphConfig) -> list[str]:
    errors: list[str] = []
    task_counter = 0
    for phase_index, phase in enumerate(plan.phases, start=1):
        for task in phase.tasks:
            task_counter += 1
            task_id = f"{task_counter:02d}"
            try:
                task_doc = task.to_task_json(phase_id=phase_index, task_id=task_id)
            except ValidationError as exc:
                errors.append(f"Task {task_id}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Task {task_id}: {exc}")
                continue
            errors.extend(_validate_task_semantics(task_doc, config, source_label=f"Task {task_id}"))
    return errors


def _resolve_plan_output_path(
    *,
    config: RalphConfig,
    runtime_paths: _Paths,
    plan_output_file: Path | None,
) -> Path:
    candidate = plan_output_file or (runtime_paths.tmp_dir / "generated-plan.json")
    return candidate.expanduser().resolve()


def _build_plan_validation_command(plan_output_path: Path, config_path: Path) -> str:
    return " ".join(
        [
            shlex.quote(sys.executable),
            "-m",
            "ralph_loop",
            "validate-plan",
            "--config",
            shlex.quote(str(config_path)),
            "--plan-file",
            shlex.quote(str(plan_output_path)),
        ]
    )


def _load_generated_plan_from_file(plan_output_path: Path) -> GeneratedPlan:
    if not plan_output_path.exists():
        raise ValueError(f"Plan output file was not created: {plan_output_path}")
    try:
        payload = json.loads(plan_output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Plan output file does not contain valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Plan output file root must be an object: {plan_output_path}")
    return GeneratedPlan.model_validate(payload)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """ralph-loop command line interface."""


@main.command("status")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
def status_command(config_path: str, loop_dir: str) -> None:
    """Print a summary of PROGRESS.yaml."""
    config, progress = _load_config_and_progress(config_path, loop_dir)

    click.echo("ralph-loop status")
    click.echo("─────────────────────────────────")

    completed = 0
    in_progress = 0
    not_started = 0

    for phase in progress.phases:
        click.echo(f"Phase {phase.id}: {phase.name} ({_phase_status_label(phase.status)})")
        for task in phase.tasks:
            icon = _task_label(task.status, task.retries, config.max_retries)
            suffix = ""
            if task.status == TaskStatus.FAILED:
                suffix = f" (attempt {task.retries}/{config.max_retries})"
            click.echo(f"  {icon} {task.id} - {task.title}{suffix}")

            if task.status == TaskStatus.COMPLETED:
                completed += 1
            elif task.status == TaskStatus.IN_PROGRESS:
                in_progress += 1
            else:
                not_started += 1
        click.echo("")

    total = completed + in_progress + not_started
    click.echo("─────────────────────────────────")
    click.echo(
        f"Progress: {completed}/{total} completed, {in_progress} in progress, {not_started} not started"
    )


@main.command("list-engines")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
def list_engines_command(config_path: str) -> None:
    """Print unique engine names from configured backend roles."""
    config = _load_config(config_path)
    engines = sorted({backend.engine for backend in config.backends.values()})
    for engine in engines:
        click.echo(engine)


@main.command("init-engine")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
def init_engine_command(config_path: str) -> None:
    """Print the engine used by `init` plan generation."""
    config = _load_config(config_path)
    role_backend = _select_init_backend(config)
    click.echo(role_backend.engine)


@main.command("auth-config")
@click.option("--engine", required=True)
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
def auth_config_command(engine: str, config_path: str) -> None:
    """Print auth forwarding config for an engine as JSON."""
    config = _load_config(config_path)
    click.echo(json.dumps(config.get_auth(engine).model_dump()))


@main.command("validate")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
def validate_command(config_path: str, loop_dir: str) -> None:
    """Validate consistency between config, progress, and task files."""
    config, progress = _load_config_and_progress(config_path, loop_dir)
    validation_errors = _collect_validate_errors(config, progress)
    if validation_errors:
        raise click.ClickException("\n".join(validation_errors))

    click.echo("Validation passed.")


@main.command("check")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
def check_command(config_path: str, loop_dir: str) -> None:
    """Run semantic checks on a generated ralph-loop."""
    config, progress = _load_config_and_progress(config_path, loop_dir)
    validation_errors = _collect_validate_errors(config, progress)
    check_errors = _collect_loop_check_errors(
        config_path=Path(config_path).expanduser().resolve(),
        loop_dir=Path(loop_dir).expanduser().resolve(),
        config=config,
        progress=progress,
    )
    errors = [*validation_errors, *check_errors]
    if errors:
        raise click.ClickException("\n".join(errors))
    click.echo("Loop checks passed.")


@main.command("validate-plan")
@click.option("--plan-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
def validate_plan_command(plan_file: Path, config_path: str) -> None:
    """Validate an init-generated plan JSON before writing loop artifacts."""
    config = _load_config(config_path)
    try:
        plan = _load_generated_plan_from_file(plan_file)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc

    errors = _validate_generated_plan_tasks(plan)
    errors.extend(_validate_generated_plan_semantics(plan, config))
    if errors:
        raise click.ClickException("\n".join(errors))
    click.echo("Plan validation passed.")


@main.command("next-action")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
@click.option("--step-result", "step_result_path", required=False)
def next_action_command(config_path: str, loop_dir: str, step_result_path: str | None) -> None:
    """Determine next orchestration action."""
    config = _load_config(config_path, loop_dir)
    progress = load_progress(config.progress_file)
    paths = _runtime_paths(config)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)
    unified_mode = config.review_mode == "unified_agent"

    if Path(config.pause_file).exists():
        click.echo(
            json.dumps(
                {
                    "command": "pause",
                    "reason": "PAUSE.md found",
                    "pause_file": config.pause_file,
                }
            )
        )
        return

    iteration = _load_iteration_state(paths.iteration_path)
    step_result = _extract_step_result(step_result_path)
    if iteration is None and isinstance(step_result, dict):
        step_name = str(step_result.get("step", ""))
        if step_name == "update":
            if bool(step_result.get("commit_required")):
                task_id = str(step_result.get("task_id", "")).strip()
                task_title = str(step_result.get("task_title", "")).strip() or task_id
                commit_mode = str(step_result.get("commit_mode", "none")).strip()
                progress_file = str(step_result.get("progress_file", config.progress_file)).strip()
                latest_feedback_summary = str(
                    step_result.get("latest_feedback_summary", "Task updated.")
                ).strip()
                click.echo(
                    json.dumps(
                        _emit_commit_action(
                            task_id=task_id,
                            task_title=task_title,
                            commit_mode=commit_mode,
                            progress_file=progress_file,
                            latest_feedback_summary=latest_feedback_summary,
                            config=config,
                        )
                    )
                )
                return
        elif step_name == "commit":
            if int(step_result.get("exit_code", 1)) != 0:
                click.echo(
                    json.dumps(
                        {
                            "command": "abort",
                            "task_id": str(step_result.get("task_id", "")),
                            "reason": "commit_failed",
                        }
                    )
                )
                return

    if iteration is not None and step_result is not None:
        _append_step_result(iteration, step_result)

        task_id = str(iteration.get("task_id", ""))
        task = find_task(progress, task_id)
        step_name = str(step_result.get("step", ""))

        if unified_mode:
            if step_name == _runtime_step_name("pre_code"):
                runtime_exit = int(step_result.get("exit_code", 1))
                if runtime_exit == 0:
                    iteration["runtime_pre_ok"] = True
                    iteration["current_step"] = "code"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(json.dumps(_emit_code_action(task=task, config=config, paths=paths)))
                    return

                guard = _runtime_guard_for_phase(config, "pre_code")
                policy = guard.on_failure if guard is not None else "fail_attempt"
                iteration["runtime_pre_ok"] = False
                _save_iteration_state(paths.iteration_path, iteration)

                if policy == "fail_attempt":
                    iteration["current_step"] = "update"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(json.dumps({"command": "update", "task_id": task.id}))
                    return

                if policy == "abort_loop":
                    click.echo(
                        json.dumps(
                            {
                                "command": "abort",
                                "task_id": task.id,
                                "reason": "runtime_pre_code_failed",
                            }
                        )
                    )
                    return

                click.echo(
                    json.dumps(
                        {
                            "command": "pause",
                            "task_id": task.id,
                            "reason": "runtime_pre_code_failed",
                        }
                    )
                )
                return

            if step_name == "code":
                code_exit = int(step_result.get("exit_code", 1))
                if code_exit != 0:
                    iteration["current_step"] = "update"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(json.dumps({"command": "update", "task_id": task.id}))
                    return

                runtime_post_action = _emit_runtime_action(task=task, config=config, phase="post_code")
                if runtime_post_action is not None:
                    iteration["current_step"] = "runtime_post_code"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(json.dumps(runtime_post_action))
                    return

                iteration["current_step"] = "review"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(
                    json.dumps(
                        _emit_review_action(
                            task=task,
                            config=config,
                            paths=paths,
                            iteration=iteration,
                        )
                    )
                )
                return

            if step_name == _runtime_step_name("post_code"):
                runtime_exit = int(step_result.get("exit_code", 1))
                if runtime_exit != 0:
                    iteration["current_step"] = "update"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(json.dumps({"command": "update", "task_id": task.id}))
                    return

                iteration["current_step"] = "review"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(
                    json.dumps(
                        _emit_review_action(
                            task=task,
                            config=config,
                            paths=paths,
                            iteration=iteration,
                        )
                    )
                )
                return

            if step_name in {"review", "verify", "visual", "inspect"}:
                iteration["current_step"] = "update"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(json.dumps({"command": "update", "task_id": task.id}))
                return

        if step_name == "code":
            verify_commands = _task_verify_commands(task, config)
            visual_config = _task_visual_verify(task, config) or task.visual_verify
            if verify_commands:
                iteration["current_step"] = "verify"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(
                    json.dumps(
                        {
                            "command": "verify",
                            "task_id": task.id,
                            "commands": verify_commands,
                            "workspace_dir": config.workspace_dir,
                        }
                    )
                )
                return

            if visual_config is not None:
                visual_role = _visual_backend_role(config)
                visual = config.get_backend(visual_role)
                visual_flags = _with_dynamic_codex_reasoning(
                    visual,
                    step="visual",
                    task_retries=task.retries,
                )
                iteration["current_step"] = "visual"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(
                    json.dumps(
                        {
                            "command": "visual",
                            "task_id": task.id,
                            "image": f"ralph-loop-{visual.engine}",
                            "model": visual.model,
                            "timeout_seconds": visual.timeout_seconds,
                            "extra_flags": visual_flags,
                            "workspace_dir": config.workspace_dir,
                            "auth": config.get_auth(visual.engine).model_dump(),
                        }
                    )
                )
                return

            inspector_prompt = _build_inspector_prompt(_task_for_inspector(task, config), None)
            paths.inspector_prompt_path.write_text(inspector_prompt, encoding="utf-8")
            inspector = config.get_backend("inspector")
            inspector_flags = _with_dynamic_codex_reasoning(
                inspector,
                step="inspect",
                task_retries=task.retries,
            )
            iteration["current_step"] = "inspect"
            _save_iteration_state(paths.iteration_path, iteration)
            click.echo(
                json.dumps(
                    {
                        "command": "inspect",
                        "task_id": task.id,
                        "image": f"ralph-loop-{inspector.engine}",
                        "prompt_file": str(paths.inspector_prompt_path),
                        "model": inspector.model,
                        "timeout_seconds": inspector.timeout_seconds,
                        "extra_flags": inspector_flags,
                        "auth": config.get_auth(inspector.engine).model_dump(),
                    }
                )
            )
            return

        if step_name == "verify":
            visual_config = _task_visual_verify(task, config) or task.visual_verify
            if visual_config is not None:
                visual_role = _visual_backend_role(config)
                visual = config.get_backend(visual_role)
                visual_flags = _with_dynamic_codex_reasoning(
                    visual,
                    step="visual",
                    task_retries=task.retries,
                )
                iteration["current_step"] = "visual"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(
                    json.dumps(
                        {
                            "command": "visual",
                            "task_id": task.id,
                            "image": f"ralph-loop-{visual.engine}",
                            "model": visual.model,
                            "timeout_seconds": visual.timeout_seconds,
                            "extra_flags": visual_flags,
                            "workspace_dir": config.workspace_dir,
                            "auth": config.get_auth(visual.engine).model_dump(),
                        }
                    )
                )
                return

            verification_results = _collect_verification_results(iteration)
            inspector_prompt = _build_inspector_prompt(
                _task_for_inspector(task, config), verification_results
            )
            paths.inspector_prompt_path.write_text(inspector_prompt, encoding="utf-8")
            inspector = config.get_backend("inspector")
            inspector_flags = _with_dynamic_codex_reasoning(
                inspector,
                step="inspect",
                task_retries=task.retries,
            )
            iteration["current_step"] = "inspect"
            _save_iteration_state(paths.iteration_path, iteration)
            click.echo(
                json.dumps(
                    {
                        "command": "inspect",
                        "task_id": task.id,
                        "image": f"ralph-loop-{inspector.engine}",
                        "prompt_file": str(paths.inspector_prompt_path),
                        "model": inspector.model,
                        "timeout_seconds": inspector.timeout_seconds,
                        "extra_flags": inspector_flags,
                        "auth": config.get_auth(inspector.engine).model_dump(),
                    }
                )
            )
            return

        if step_name == "visual":
            verification_results = _collect_verification_results(iteration)
            inspector_prompt = _build_inspector_prompt(
                _task_for_inspector(task, config), verification_results
            )
            paths.inspector_prompt_path.write_text(inspector_prompt, encoding="utf-8")
            inspector = config.get_backend("inspector")
            inspector_flags = _with_dynamic_codex_reasoning(
                inspector,
                step="inspect",
                task_retries=task.retries,
            )
            iteration["current_step"] = "inspect"
            _save_iteration_state(paths.iteration_path, iteration)
            click.echo(
                json.dumps(
                    {
                        "command": "inspect",
                        "task_id": task.id,
                        "image": f"ralph-loop-{inspector.engine}",
                        "prompt_file": str(paths.inspector_prompt_path),
                        "model": inspector.model,
                        "timeout_seconds": inspector.timeout_seconds,
                        "extra_flags": inspector_flags,
                        "auth": config.get_auth(inspector.engine).model_dump(),
                    }
                )
            )
            return

        if step_name == "inspect":
            iteration["current_step"] = "update"
            _save_iteration_state(paths.iteration_path, iteration)
            click.echo(json.dumps({"command": "update", "task_id": task.id}))
            return

    if iteration is not None and step_result is None:
        current_step = str(iteration.get("current_step", ""))
        task_id = str(iteration.get("task_id", ""))
        if current_step == "update":
            _clear_iteration_state(paths.iteration_path)
            iteration = None
        elif task_id:
            task = find_task(progress, task_id)
            if unified_mode:
                if current_step == "runtime_pre_code":
                    runtime_pre_action = _emit_runtime_action(
                        task=task, config=config, phase="pre_code"
                    )
                    if runtime_pre_action is not None:
                        click.echo(json.dumps(runtime_pre_action))
                        return
                    iteration["current_step"] = "code"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(json.dumps(_emit_code_action(task=task, config=config, paths=paths)))
                    return
                if current_step == "code":
                    click.echo(json.dumps(_emit_code_action(task=task, config=config, paths=paths)))
                    return
                if current_step == "runtime_post_code":
                    runtime_post_action = _emit_runtime_action(
                        task=task, config=config, phase="post_code"
                    )
                    if runtime_post_action is not None:
                        click.echo(json.dumps(runtime_post_action))
                        return
                    iteration["current_step"] = "review"
                    _save_iteration_state(paths.iteration_path, iteration)
                    click.echo(
                        json.dumps(
                            _emit_review_action(
                                task=task,
                                config=config,
                                paths=paths,
                                iteration=iteration,
                            )
                        )
                    )
                    return
                if current_step == "review":
                    click.echo(
                        json.dumps(
                            _emit_review_action(
                                task=task,
                                config=config,
                                paths=paths,
                                iteration=iteration,
                            )
                        )
                    )
                    return
            else:
                if current_step == "code":
                    click.echo(json.dumps(_emit_code_action(task=task, config=config, paths=paths)))
                    return
                if current_step == "verify":
                    click.echo(
                        json.dumps(
                            {
                                "command": "verify",
                                "task_id": task.id,
                                "commands": _task_verify_commands(task, config),
                                "workspace_dir": config.workspace_dir,
                            }
                        )
                    )
                    return
                if current_step == "visual":
                    visual_config = _task_visual_verify(task, config) or task.visual_verify
                    if visual_config is not None:
                        visual_role = _visual_backend_role(config)
                        visual = config.get_backend(visual_role)
                        visual_flags = _with_dynamic_codex_reasoning(
                            visual,
                            step="visual",
                            task_retries=task.retries,
                        )
                        click.echo(
                            json.dumps(
                                {
                                    "command": "visual",
                                    "task_id": task.id,
                                    "image": f"ralph-loop-{visual.engine}",
                                    "model": visual.model,
                                    "timeout_seconds": visual.timeout_seconds,
                                    "extra_flags": visual_flags,
                                    "workspace_dir": config.workspace_dir,
                                    "auth": config.get_auth(visual.engine).model_dump(),
                                }
                            )
                        )
                        return
                if current_step == "inspect":
                    inspector_prompt = _build_inspector_prompt(_task_for_inspector(task, config), None)
                    paths.inspector_prompt_path.write_text(inspector_prompt, encoding="utf-8")
                    inspector = config.get_backend("inspector")
                    inspector_flags = _with_dynamic_codex_reasoning(
                        inspector,
                        step="inspect",
                        task_retries=task.retries,
                    )
                    click.echo(
                        json.dumps(
                            {
                                "command": "inspect",
                                "task_id": task.id,
                                "image": f"ralph-loop-{inspector.engine}",
                                "prompt_file": str(paths.inspector_prompt_path),
                                "model": inspector.model,
                                "timeout_seconds": inspector.timeout_seconds,
                                "extra_flags": inspector_flags,
                                "auth": config.get_auth(inspector.engine).model_dump(),
                            }
                        )
                    )
                    return

    if iteration is not None and iteration.get("current_step") == "update":
        _clear_iteration_state(paths.iteration_path)
        iteration = None

    if iteration is None:
        in_progress_tasks = find_in_progress_tasks(progress)
        if in_progress_tasks:
            click.echo(
                json.dumps(
                    {
                        "command": "orphan",
                        "task_ids": [task.id for task in in_progress_tasks],
                        "reason": "orphan_in_progress",
                    }
                )
            )
            return

    aborted_task = _first_aborted_task(progress)
    if aborted_task is not None:
        click.echo(
            json.dumps(
                {
                    "command": "abort",
                    "task_id": aborted_task.id,
                    "reason": "task_aborted",
                }
            )
        )
        return

    next_task = select_next_task(progress, config.max_retries)
    if next_task is None:
        summary = _summary(progress)
        reason = "all_completed" if summary["aborted"] == 0 else "remaining_tasks_aborted"
        click.echo(json.dumps({"command": "done", "reason": reason, "summary": summary}))
        return

    lock_task(progress, next_task.id)
    save_progress(progress, config.progress_file)
    coder_prompt = _build_coder_prompt(next_task, config)
    paths.coder_prompt_path.write_text(coder_prompt, encoding="utf-8")

    initial_step = "code"
    task_base_sha: str | None = None
    if unified_mode:
        task_base_sha = _capture_head_sha(config.workspace_dir)
        initial_step = "runtime_pre_code" if config.runtime_guards.pre_code is not None else "code"

    iteration = {
        "task_id": next_task.id,
        "current_step": initial_step,
        "started_at": _now_iso(),
        "results": [],
        "task_base_sha": task_base_sha,
        "runtime_pre_ok": False,
    }
    _save_iteration_state(paths.iteration_path, iteration)

    if unified_mode and initial_step == "runtime_pre_code":
        runtime_pre_action = _emit_runtime_action(task=next_task, config=config, phase="pre_code")
        if runtime_pre_action is not None:
            click.echo(json.dumps(runtime_pre_action))
            return

    click.echo(json.dumps(_emit_code_action(task=next_task, config=config, paths=paths)))


@main.command("run")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
@click.option("--sandbox", type=click.Choice(["none", "docker"]), default="none", show_default=True)
def run_command(config_path: str, loop_dir: str, sandbox: str) -> None:
    """Run the native orchestration loop."""
    config = _load_config(config_path, loop_dir)
    if config.review_mode == "unified_agent":
        raise click.ClickException(
            "Native `ralph-loop run` does not support `review_mode=unified_agent` yet. "
            "Use the docker dispatcher script `./ralph-loop run`."
        )
    raise SystemExit(run_loop(config, sandbox=sandbox))


@main.command("execute")
@click.option("--prompt-file", required=True)
@click.option("--model", required=False)
@click.option("--timeout-seconds", type=int, default=600, show_default=True)
@click.option("--extra-flag", "extra_flags", multiple=True)
def execute_command(
    prompt_file: str, model: str | None, timeout_seconds: int, extra_flags: tuple[str, ...]
) -> None:
    """Execute a coding prompt using an available AI CLI backend."""
    exit_code = _run_prompt_with_available_backend(
        Path(prompt_file),
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=list(extra_flags),
    )
    if exit_code != 0:
        raise SystemExit(exit_code)


@main.command("inspect")
@click.option("--prompt-file", required=True)
@click.option("--model", required=False)
@click.option("--timeout-seconds", type=int, default=600, show_default=True)
@click.option("--extra-flag", "extra_flags", multiple=True)
def inspect_command(
    prompt_file: str, model: str | None, timeout_seconds: int, extra_flags: tuple[str, ...]
) -> None:
    """Execute an inspection prompt using an available AI CLI backend."""
    exit_code = _run_prompt_with_available_backend(
        Path(prompt_file),
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=list(extra_flags),
    )
    if exit_code != 0:
        raise SystemExit(exit_code)


@main.command("review")
@click.option("--prompt-file", required=True)
@click.option("--model", required=False)
@click.option("--timeout-seconds", type=int, default=600, show_default=True)
@click.option("--extra-flag", "extra_flags", multiple=True)
def review_command(
    prompt_file: str, model: str | None, timeout_seconds: int, extra_flags: tuple[str, ...]
) -> None:
    """Execute a unified reviewer prompt using an available AI CLI backend."""
    exit_code = _run_prompt_with_available_backend(
        Path(prompt_file),
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=list(extra_flags),
    )
    if exit_code != 0:
        raise SystemExit(exit_code)


@main.command("visual")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
@click.option("--task-id", required=True)
@click.option("--model", required=False)
@click.option("--timeout-seconds", type=int, default=300, show_default=True)
@click.option("--extra-flag", "extra_flags", multiple=True)
def visual_command(
    config_path: str,
    loop_dir: str,
    task_id: str,
    model: str | None,
    timeout_seconds: int,
    extra_flags: tuple[str, ...],
) -> None:
    """Run visual verification and print JSON verdict."""
    config = _load_config(config_path, loop_dir)
    progress = load_progress(config.progress_file)
    task_progress = find_task(progress, task_id)
    task_path = _resolve_task_file_path(task_progress.task_file, config)
    if task_path is None:
        raise click.ClickException(f"Task file not found: {task_progress.task_file}")

    task_doc = _load_task_document(task_progress, config)
    if task_doc is None:
        raise click.ClickException(f"Task file not found: {task_progress.task_file}")
    if isinstance(task_doc, TaskJson):
        task = task_doc.to_legacy_task(task_path)
        visual_config = task_doc.visual_verify
    else:
        task = task_doc
        visual_config = task_doc.frontmatter.visual_verify

    _, backend = _select_available_backend()
    inspector_cfg = config.get_backend("inspector")
    visual_config = visual_config or task_progress.visual_verify
    result = run_visual_verification(
        config=visual_config,
        workspace_dir=config.workspace_dir,
        backend=backend,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=list(extra_flags),
        task=task,
        inspector_backend=backend,
        inspector_model=inspector_cfg.model,
        inspector_timeout_seconds=inspector_cfg.timeout_seconds,
        inspector_extra_flags=inspector_cfg.extra_flags,
    )
    click.echo(json.dumps({"verdict": result.verdict, "feedback": result.details}))


@main.command("update")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
@click.option("--result-dir", required=True)
def update_command(config_path: str, loop_dir: str, result_dir: str) -> None:
    """Aggregate step results and update progress."""
    config = _load_config(config_path, loop_dir)
    progress = load_progress(config.progress_file)
    paths = _runtime_paths(config)
    iteration = _load_iteration_state(paths.iteration_path)
    if iteration is None:
        raise click.ClickException("iteration-state.json not found")

    task_id = str(iteration.get("task_id", ""))
    if not task_id:
        raise click.ClickException("Invalid iteration state: missing task_id")

    task = find_task(progress, task_id)
    results = _collect_iteration_results(Path(result_dir), iteration)

    if config.review_mode == "unified_agent":
        all_passed = True
        feedback_sources: list[FeedbackSource] = []
        has_review_result = False
        code_executed = False
        code_success = False
        runtime_pre_failed = False
        runtime_post_failed = False

        for item in results:
            step_name = str(item.get("step", ""))
            if step_name == "code":
                code_executed = True
                exit_code = int(item.get("exit_code", 1))
                code_success = exit_code == 0
                if not code_success:
                    all_passed = False
                    feedback_sources.append(
                        FeedbackSource(
                            type="code",
                            verdict="fail",
                            details=f"Code step failed with exit code {exit_code}",
                        )
                    )
                continue

            if step_name == "verify":
                command_results = item.get("results")
                if isinstance(command_results, list):
                    for result in command_results:
                        if not isinstance(result, dict):
                            continue
                        command = str(result.get("command", ""))
                        exit_code = int(result.get("exit_code", 1))
                        stdout = str(result.get("stdout", ""))
                        stderr = str(result.get("stderr", ""))
                        verdict = "pass" if exit_code == 0 else "fail"
                        if verdict == "fail":
                            all_passed = False
                        combined = (stdout + "\n" + stderr).strip()
                        feedback_sources.append(
                            FeedbackSource(
                                type="test",
                                verdict=verdict,
                                command=command,
                                exit_code=exit_code,
                                output=combined,
                            )
                        )
                continue

            if step_name in {"runtime_pre_code", "runtime_post_code"}:
                exit_code = int(item.get("exit_code", 1))
                runtime_command = str(item.get("runtime_command", ""))
                stdout = str(item.get("stdout", ""))
                stderr = str(item.get("stderr", ""))
                if exit_code != 0:
                    all_passed = False
                    if step_name == "runtime_pre_code":
                        runtime_pre_failed = True
                    if step_name == "runtime_post_code":
                        runtime_post_failed = True
                    feedback_sources.append(
                        FeedbackSource(
                            type="runtime_guard",
                            verdict="fail",
                            command=runtime_command or None,
                            exit_code=exit_code,
                            output=(stdout + "\n" + stderr).strip(),
                            details=f"{step_name} failed with exit code {exit_code}",
                        )
                    )
                continue

            if step_name == "review":
                has_review_result = True
                step_exit_code = int(item.get("exit_code", 0))
                if step_exit_code != 0:
                    all_passed = False
                    feedback_sources.append(
                        FeedbackSource(
                            type="review",
                            verdict="fail",
                            details=f"Review step command failed with exit code {step_exit_code}",
                        )
                    )
                    continue

                verdict, feedback = _parse_reviewer_output(str(item.get("stdout", "")))
                if verdict != "pass":
                    all_passed = False
                feedback_sources.append(
                    FeedbackSource(type="review", verdict=verdict, details=feedback)
                )

        review_expected = code_executed and code_success and not runtime_pre_failed and not runtime_post_failed
        if review_expected and not has_review_result:
            all_passed = False
            feedback_sources.append(
                FeedbackSource(
                    type="review",
                    verdict="fail",
                    details="Review result missing for this attempt.",
                )
            )
    else:
        all_passed = True
        feedback_sources = []
        has_visual_result = False
        has_inspect_result = False
        visual_required = (_task_visual_verify(task, config) or task.visual_verify) is not None

        for item in results:
            step_name = str(item.get("step", ""))
            if step_name == "code":
                exit_code = int(item.get("exit_code", 1))
                if exit_code != 0:
                    all_passed = False
                    feedback_sources.append(
                        FeedbackSource(
                            type="code",
                            verdict="fail",
                            details=f"Code step failed with exit code {exit_code}",
                        )
                    )
                continue

            if step_name == "verify":
                command_results = item.get("results")
                if isinstance(command_results, list):
                    for result in command_results:
                        if not isinstance(result, dict):
                            continue
                        command = str(result.get("command", ""))
                        exit_code = int(result.get("exit_code", 1))
                        stdout = str(result.get("stdout", ""))
                        stderr = str(result.get("stderr", ""))
                        verdict = "pass" if exit_code == 0 else "fail"
                        if verdict == "fail":
                            all_passed = False
                        combined = (stdout + "\n" + stderr).strip()
                        feedback_sources.append(
                            FeedbackSource(
                                type="test",
                                verdict=verdict,
                                command=command,
                                exit_code=exit_code,
                                output=combined,
                            )
                        )
                continue

            if step_name == "visual":
                has_visual_result = True
                verdict, feedback = _parse_inspector_output(str(item.get("stdout", "")))
                step_exit_code = int(item.get("exit_code", 0))
                if step_exit_code != 0:
                    verdict = "fail"
                    feedback = feedback or f"Visual step command failed with exit code {step_exit_code}"
                if verdict != "pass":
                    all_passed = False
                feedback_sources.append(
                    FeedbackSource(type="visual", verdict=verdict, details=feedback)
                )
                continue

            if step_name == "inspect":
                has_inspect_result = True
                verdict, feedback = _parse_inspector_output(str(item.get("stdout", "")))
                if verdict != "pass":
                    all_passed = False
                feedback_sources.append(
                    FeedbackSource(type="ai_inspection", verdict=verdict, details=feedback)
                )

        if visual_required and not has_visual_result:
            all_passed = False
            feedback_sources.append(
                FeedbackSource(
                    type="visual",
                    verdict="fail",
                    details="Visual verification was required but no visual result was recorded.",
                )
            )

        if not has_inspect_result:
            all_passed = False
            feedback_sources.append(
                FeedbackSource(
                    type="ai_inspection",
                    verdict="fail",
                    details="Inspector result missing for this attempt.",
                )
            )

    if all_passed:
        complete_task(progress, task.id)
    else:
        entry = FeedbackEntry(
            attempt=task.retries + 1, timestamp=_now_iso(), sources=feedback_sources
        )
        fail_task(progress, task.id, entry, config.max_retries)

    status_after_update = task.status.value
    commit_required = False
    commit_mode = "none"
    if status_after_update == TaskStatus.COMPLETED.value:
        commit_required = True
        commit_mode = "approved_task"
    elif status_after_update == TaskStatus.FAILED.value:
        commit_required = True
        commit_mode = "progress_only_failed_attempt"

    latest_feedback_summary = _summarize_feedback_sources(feedback_sources)
    save_progress(progress, config.progress_file)
    _clear_iteration_state(paths.iteration_path)
    click.echo(
        json.dumps(
            {
                "step": "update",
                "task_id": task.id,
                "task_title": task.title,
                "task_status_after_update": status_after_update,
                "commit_required": commit_required,
                "commit_mode": commit_mode,
                "progress_file": config.progress_file,
                "latest_feedback_summary": latest_feedback_summary,
            }
        )
    )


@main.command("reset")
@click.argument("task_id")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
def reset_command(task_id: str, config_path: str, loop_dir: str) -> None:
    """Reset a task to not_started state."""
    config, progress = _load_config_and_progress(config_path, loop_dir)

    found = False
    for phase in progress.phases:
        for task in phase.tasks:
            if task.id == task_id:
                found = True
                task.status = TaskStatus.NOT_STARTED
                task.retries = 0
                task.feedback = []
    if not found:
        raise click.ClickException(f"Task not found: {task_id}")

    from ralph_loop.progress import save_progress

    save_progress(progress, config.progress_file)
    click.echo(f"Task reset: {task_id}")


@main.command("recover")
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
@click.option("--loop-dir", default=".", show_default=True)
@click.option("--task-id", default="all", show_default=True)
def recover_command(config_path: str, loop_dir: str, task_id: str) -> None:
    """Recover orphan tasks left in in_progress state."""
    config, progress = _load_config_and_progress(config_path, loop_dir)

    if task_id == "all":
        task_ids = [task.id for task in find_in_progress_tasks(progress)]
    else:
        task_ids = [task_id]

    recovered: list[str] = []
    for pending_task_id in task_ids:
        recover_in_progress_task(progress, pending_task_id, config.max_retries)
        recovered.append(pending_task_id)

    if recovered:
        save_progress(progress, config.progress_file)

    click.echo(json.dumps({"recovered": recovered}))


@main.command("init")
@click.option("--from", "source_path", required=True)
@click.option("--backend", required=False)
@click.option("--model", required=False)
@click.option(
    "--instructions", required=False, help="Additional user directives for plan generation."
)
@click.option(
    "--instructions-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
    help="Path to a file containing additional directives.",
)
@click.option(
    "--plan-output-file",
    type=click.Path(dir_okay=False, path_type=Path),
    required=False,
    help="Path where init should ask the backend to write generated-plan.json.",
)
@click.option("--config", "config_path", default=_default_config_path, show_default="auto")
def init_command(
    source_path: str,
    backend: str | None,
    model: str | None,
    instructions: str | None,
    instructions_file: Path | None,
    plan_output_file: Path | None,
    config_path: str,
) -> None:
    """Generate tasks and progress from a plan document."""
    source = Path(source_path)
    if not source.exists():
        raise click.ClickException(f"Source plan not found: {source}")

    loop_dir = _derive_loop_dir_from_source(source)
    config_file = Path(config_path)
    click.echo(f"[init] Loading config: {config_file}")
    click.echo(f"[init] Loop directory: {loop_dir}")
    config = _ensure_config(config_file, loop_dir=loop_dir)
    context = _InitContext(source_path=source, config=config)
    _ensure_init_directories(config)
    runtime_paths = _runtime_paths(config)
    runtime_paths.tmp_dir.mkdir(parents=True, exist_ok=True)
    resolved_plan_output_path = _resolve_plan_output_path(
        config=config,
        runtime_paths=runtime_paths,
        plan_output_file=plan_output_file,
    )
    resolved_plan_output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_config_path = config_file.expanduser().resolve()

    click.echo(f"[init] Reading source design: {source}")
    source_content = source.read_text(encoding="utf-8")
    user_directives = _load_user_directives(instructions, instructions_file)
    if user_directives:
        click.echo("[init] Applying additional user directives.")
    click.echo(f"[init] Generated plan output file: {resolved_plan_output_path}")
    prompt = _build_prompt(
        source_content,
        config,
        plan_output_path=resolved_plan_output_path,
        plan_validation_command=_build_plan_validation_command(
            resolved_plan_output_path,
            resolved_config_path,
        ),
        user_directives=user_directives,
    )

    role_backend = _select_init_backend(config)
    selected_engine = backend or role_backend.engine
    selected_model = model or role_backend.model
    if selected_model:
        click.echo(
            f"[init] Generating plan with backend '{selected_engine}' (model: {selected_model})"
        )
    else:
        click.echo(f"[init] Generating plan with backend '{selected_engine}'")

    generated_plan: GeneratedPlan | None = None
    validation_feedback: str | None = None
    attempts = 3
    previous_copilot_stream_setting = os.environ.get("RALPH_COPILOT_STREAM_OUTPUT")
    previous_codex_stream_setting = os.environ.get("RALPH_CODEX_STREAM_OUTPUT")
    os.environ["RALPH_COPILOT_STREAM_OUTPUT"] = "1"
    os.environ["RALPH_CODEX_STREAM_OUTPUT"] = "1"
    try:
        for attempt in range(1, attempts + 1):
            candidate_plan, generation_error, generation_result, prompt_used = (
                _generate_plan_with_backend(
                    config=config,
                    prompt=prompt,
                    plan_output_path=resolved_plan_output_path,
                    backend_override=backend,
                    model_override=model,
                    validation_feedback=validation_feedback,
                )
            )
            prompt_path, stdout_path, stderr_path = _write_init_attempt_artifacts(
                tmp_dir=runtime_paths.tmp_dir,
                attempt=attempt,
                prompt_text=prompt_used,
                result=generation_result,
            )

            if generation_result is not None:
                runtime_seconds = float(getattr(generation_result, "duration_seconds", 0.0))
                result_exit_code = int(getattr(generation_result, "exit_code", 1))
                timed_out = bool(getattr(generation_result, "timed_out", False))
                click.echo(
                    "[init] Attempt "
                    f"{attempt}/{attempts} backend runtime: {runtime_seconds:.1f}s "
                    f"(exit={result_exit_code}, timed_out={timed_out})"
                )
                click.echo(f"[init] Prompt artifact: {prompt_path}")
                if stdout_path is not None:
                    click.echo(f"[init] Backend stdout artifact: {stdout_path}")
                if stderr_path is not None:
                    click.echo(f"[init] Backend stderr artifact: {stderr_path}")

            if candidate_plan is None:
                validation_feedback = generation_error or "Unknown generation error"
                click.echo(f"[init] Attempt {attempt}/{attempts} failed: {validation_feedback}")
                if generation_result is not None:
                    stderr_preview = _truncate_for_log(
                        str(getattr(generation_result, "stderr", "") or "")
                    )
                    stdout_preview = _truncate_for_log(
                        str(getattr(generation_result, "stdout", "") or "")
                    )
                    if stderr_preview:
                        click.echo("[init] stderr preview:")
                        click.echo(stderr_preview)
                    if stdout_preview:
                        click.echo("[init] stdout preview:")
                        click.echo(stdout_preview)
                continue

            candidate_plan = _apply_plan_hints(
                candidate_plan,
                f"{source_content}\n\n{user_directives}",
            )
            task_errors = _validate_generated_plan_tasks(candidate_plan)
            task_errors.extend(_validate_generated_plan_semantics(candidate_plan, config))
            if task_errors:
                validation_feedback = "\n".join(task_errors)
                click.echo(f"[init] Attempt {attempt}/{attempts} failed task schema validation.")
                continue

            generated_plan = candidate_plan
            break
    finally:
        if previous_copilot_stream_setting is None:
            os.environ.pop("RALPH_COPILOT_STREAM_OUTPUT", None)
        else:
            os.environ["RALPH_COPILOT_STREAM_OUTPUT"] = previous_copilot_stream_setting

        if previous_codex_stream_setting is None:
            os.environ.pop("RALPH_CODEX_STREAM_OUTPUT", None)
        else:
            os.environ["RALPH_CODEX_STREAM_OUTPUT"] = previous_codex_stream_setting

    if generated_plan is None:
        final_error = validation_feedback or "Unknown generation error"
        raise click.ClickException(
            f"AI generation unavailable or invalid backend output after 3 attempts.\n{final_error}"
        )

    click.echo("[init] AI generation completed.")

    click.echo("[init] Writing tasks and progress files...")
    count, task_dir, progress_path = _write_generated_artifacts(context, generated_plan)
    progress = load_progress(config.progress_file)
    validate_errors = _collect_validate_errors(config, progress)
    check_errors = _collect_loop_check_errors(
        config_path=resolved_config_path,
        loop_dir=loop_dir,
        config=config,
        progress=progress,
    )
    errors = [*validate_errors, *check_errors]
    if errors:
        raise click.ClickException("\n".join(errors))
    click.echo(f"Generated {count} task file(s) in {task_dir}")
    click.echo(f"Generated progress file: {progress_path}")
