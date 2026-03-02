from __future__ import annotations

import signal
import time
from pathlib import Path
from types import FrameType

from jinja2 import Template

from ralph_loop.backends import get_backend
from ralph_loop.backends.base import Backend
from ralph_loop.backends.sandbox import SandboxBackend
from ralph_loop.config import BackendConfig, RalphConfig
from ralph_loop.feedback import format_feedback_for_prompt
from ralph_loop.progress import (
    FeedbackEntry,
    Progress,
    advance_phase,
    complete_task,
    fail_task,
    find_in_progress_tasks,
    load_progress,
    lock_task,
    recover_in_progress_task,
    save_progress,
    select_next_task,
)
from ralph_loop.task import Task
from ralph_loop.verification.pipeline import run_verification_pipeline


def run_loop(config: RalphConfig, sandbox: str = "none") -> int:
    """Run native orchestration loop for configured tasks."""
    _setup_signal_handlers()
    progress = load_progress(config.progress_file)
    if not _handle_orphan_tasks(progress, config):
        return 1
    save_progress(progress, config.progress_file)

    while True:
        if Path(config.pause_file).exists():
            print("[ralph] Paused — remove PAUSE.md to resume.")
            time.sleep(30)
            continue

        progress = load_progress(config.progress_file)
        task_progress = select_next_task(progress, config.max_retries)
        if task_progress is None:
            return 0 if _all_completed(progress) else 1

        lock_task(progress, task_progress.id)
        save_progress(progress, config.progress_file)

        task = Task.load(task_progress.task_file)
        coder_cfg = config.get_backend("coder")
        coder_backend = _make_backend(coder_cfg, config, sandbox)

        coder_prompt = _render_coder_prompt(
            task=task,
            feedback_text=format_feedback_for_prompt(task_progress.feedback),
            is_retry=task_progress.retries > 0,
            attempt_number=task_progress.retries + 1,
            max_retries=config.max_retries,
            project_instructions=_load_optional_file(config.project_instructions),
            contract_content=_load_optional_file(task_progress.contract_file),
        )
        code_result = coder_backend.execute(
            prompt=coder_prompt,
            model=coder_cfg.model,
            timeout_seconds=coder_cfg.timeout_seconds,
            extra_flags=coder_cfg.extra_flags,
            cwd=config.workspace_dir,
        )

        inspector_cfg = config.get_backend("inspector")
        inspector_backend = _make_backend(inspector_cfg, config, sandbox)
        visual_role = "visual" if "visual" in config.backends else "inspector"
        visual_cfg = config.get_backend(visual_role)
        visual_backend = _make_backend(visual_cfg, config, sandbox)
        verify_commands = task.get_verify_commands(config.verify_commands)
        report = run_verification_pipeline(
            task=task,
            task_progress=task_progress,
            config=config,
            inspector_backend=inspector_backend,
            verify_commands=verify_commands,
            visual_backend=visual_backend,
            visual_backend_config=visual_cfg,
        )

        if code_result.exit_code != 0:
            report.all_passed = False

        progress = load_progress(config.progress_file)
        if report.all_passed:
            complete_task(progress, task_progress.id)
        else:
            entry = FeedbackEntry(
                attempt=task_progress.retries + 1,
                timestamp=_now_iso(),
                sources=report.feedback_sources,
            )
            fail_task(progress, task_progress.id, entry, config.max_retries)

        save_progress(progress, config.progress_file)
        advance_phase(progress)
        save_progress(progress, config.progress_file)


def _make_backend(cfg: BackendConfig, config: RalphConfig, sandbox: str) -> Backend:
    backend = get_backend(cfg.engine)
    if sandbox == "docker":
        return SandboxBackend(backend, config.get_auth(cfg.engine), config.workspace_dir)
    return backend


def _render_coder_prompt(
    *,
    task: Task,
    feedback_text: str,
    is_retry: bool,
    attempt_number: int,
    max_retries: int,
    project_instructions: str | None,
    contract_content: str | None,
) -> str:
    template_path = Path(__file__).resolve().parent / "prompts" / "coder.md.j2"
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.render(
        task=task,
        previous_feedback=feedback_text,
        is_retry=is_retry,
        attempt_number=attempt_number,
        max_retries=max_retries,
        project_instructions=project_instructions,
        contract_content=contract_content,
    )


def _load_optional_file(path: str | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    if not candidate.exists():
        return None
    return candidate.read_text(encoding="utf-8")


def _all_completed(progress: Progress) -> bool:
    return all(
        task.status.value == "completed" for phase in progress.phases for task in phase.tasks
    )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _setup_signal_handlers() -> None:
    def handler(_signal: int, _frame: FrameType | None) -> None:
        print("\n[ralph] Interrupted. Exiting...")
        raise SystemExit(130)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


def _handle_orphan_tasks(progress: Progress, config: RalphConfig) -> bool:
    orphan_tasks = find_in_progress_tasks(progress)
    if not orphan_tasks:
        return True

    task_ids = ", ".join(task.id for task in orphan_tasks)
    print(f"[ralph] Found orphan in-progress tasks: {task_ids}")
    print("[ralph] Choose action: [r]eset tasks to failed and continue, [a]bort loop")

    while True:
        choice = input("[ralph] Action (r/a): ").strip().lower()
        if choice in {"a", "abort"}:
            print("[ralph] Loop aborted by user.")
            return False
        if choice in {"r", "reset"}:
            for task in orphan_tasks:
                recover_in_progress_task(progress, task.id, config.max_retries)
            print(f"[ralph] Recovered orphan tasks: {task_ids}")
            return True
        print("[ralph] Invalid option. Enter 'r' to reset and continue, or 'a' to abort.")
