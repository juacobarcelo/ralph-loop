from __future__ import annotations

from dataclasses import dataclass, field

from ralph_loop.backends.base import Backend
from ralph_loop.config import BackendConfig, RalphConfig
from ralph_loop.progress import FeedbackSource, TaskProgress
from ralph_loop.task import Task
from ralph_loop.verification.ai_inspection import run_ai_inspection
from ralph_loop.verification.deterministic import run_verify_commands
from ralph_loop.verification.visual import run_visual_verification


@dataclass
class VerificationReport:
    """Aggregated verification result for one task attempt."""

    all_passed: bool
    feedback_sources: list[FeedbackSource] = field(default_factory=list)


def run_verification_pipeline(
    *,
    task: Task,
    task_progress: TaskProgress,
    config: RalphConfig,
    inspector_backend: Backend,
    inspector_backend_config: BackendConfig | None = None,
    verify_commands: list[str],
    visual_backend: Backend | None = None,
    visual_backend_config: BackendConfig | None = None,
) -> VerificationReport:
    """Run all verifiers and aggregate pass/fail state and feedback sources."""
    feedback_sources: list[FeedbackSource] = []
    all_passed = True

    deterministic_results = run_verify_commands(verify_commands, config.workspace_dir)
    for result in deterministic_results:
        verdict = "pass" if result.exit_code == 0 else "fail"
        if verdict == "fail":
            all_passed = False
        combined = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        feedback_sources.append(
            FeedbackSource(
                type="test",
                verdict=verdict,
                command=result.command,
                exit_code=result.exit_code,
                output=combined,
            )
        )

    effective_visual_backend = visual_backend or inspector_backend
    if visual_backend_config is not None:
        effective_visual_config = visual_backend_config
    elif "visual" in config.backends:
        effective_visual_config = config.get_backend("visual")
    else:
        effective_visual_config = config.get_backend("inspector")

    inspector_config = inspector_backend_config or config.get_backend("inspector")

    visual_result = run_visual_verification(
        config=task_progress.visual_verify or task.frontmatter.visual_verify,
        workspace_dir=config.workspace_dir,
        backend=effective_visual_backend,
        model=effective_visual_config.model,
        timeout_seconds=effective_visual_config.timeout_seconds,
        extra_flags=effective_visual_config.extra_flags,
        task=task,
        inspector_backend=inspector_backend,
        inspector_model=inspector_config.model,
        inspector_timeout_seconds=inspector_config.timeout_seconds,
        inspector_extra_flags=inspector_config.extra_flags,
    )
    if visual_result.verdict != "pass":
        all_passed = False
    feedback_sources.append(
        FeedbackSource(type="visual", verdict=visual_result.verdict, details=visual_result.details)
    )

    inspection = run_ai_inspection(
        task=task,
        workspace_dir=config.workspace_dir,
        inspector_backend=inspector_backend,
        model=inspector_config.model,
        timeout_seconds=inspector_config.timeout_seconds,
        extra_flags=inspector_config.extra_flags,
        verify_results=deterministic_results,
        project_instructions=_load_optional_file(config.project_instructions),
        contract_content=_load_optional_file(task_progress.contract_file),
    )
    if inspection.verdict != "pass":
        all_passed = False
    feedback_sources.append(
        FeedbackSource(
            type="ai_inspection", verdict=inspection.verdict, details=inspection.feedback
        )
    )

    return VerificationReport(all_passed=all_passed, feedback_sources=feedback_sources)


def _load_optional_file(path: str | None) -> str | None:
    if path is None:
        return None
    from pathlib import Path

    file_path = Path(path)
    if not file_path.exists():
        return None
    return file_path.read_text(encoding="utf-8")
