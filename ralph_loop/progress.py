from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel, Field

from ralph_loop.config import VisualVerifyConfig


class TaskStatus(str, Enum):
    """Task lifecycle states."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORT = "abort"


class PhaseStatus(str, Enum):
    """Phase lifecycle states."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class FeedbackSource(BaseModel):
    """One verification result from one source."""

    type: str
    verdict: str
    command: str | None = None
    exit_code: int | None = None
    output: str | None = None
    details: str | None = None


class FeedbackEntry(BaseModel):
    """Accumulated feedback from one failed attempt."""

    attempt: int
    timestamp: str
    sources: list[FeedbackSource] = Field(default_factory=list)


class TaskProgress(BaseModel):
    """Runtime state of a single task."""

    id: str
    title: str
    task_file: str
    contract_file: str | None = None
    status: TaskStatus = TaskStatus.NOT_STARTED
    retries: int = 0
    verify_commands: list[str] = Field(
        default_factory=list,
        description=(
            "Resolved deterministic host-side checks for the task. In "
            "unified_agent loops this should usually remain empty."
        ),
    )
    visual_verify: VisualVerifyConfig | None = None
    feedback: list[FeedbackEntry] = Field(default_factory=list)


class PhaseProgress(BaseModel):
    """Runtime state of a phase."""

    id: int
    name: str
    status: PhaseStatus = PhaseStatus.NOT_STARTED
    tasks: list[TaskProgress] = Field(default_factory=list)


class ProgressMeta(BaseModel):
    """Project-level progress metadata."""

    title: str
    started: str
    current_phase: int = 1


class Progress(BaseModel):
    """Root model for PROGRESS.yaml."""

    meta: ProgressMeta
    phases: list[PhaseProgress] = Field(default_factory=list)


def load_progress(path: str) -> Progress:
    """Load progress file from YAML."""
    progress_path = Path(path)
    if not progress_path.exists():
        raise FileNotFoundError(f"Progress file not found: {progress_path}")
    raw = yaml.safe_load(progress_path.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Progress file is empty: {progress_path}")
    return Progress.model_validate(raw)


def save_progress(progress: Progress, path: str) -> None:
    """Persist progress atomically via tmp + rename."""
    progress_path = Path(path)
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = progress_path.with_suffix(progress_path.suffix + ".tmp")
    payload = yaml.safe_dump(progress.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, progress_path)


def get_current_phase(progress: Progress) -> PhaseProgress:
    """Return phase indicated by meta.current_phase."""
    for phase in progress.phases:
        if phase.id == progress.meta.current_phase:
            return phase
    raise ValueError(f"Current phase not found: {progress.meta.current_phase}")


def find_task(progress: Progress, task_id: str) -> TaskProgress:
    """Find task by ID across all phases."""
    for phase in progress.phases:
        for task in phase.tasks:
            if task.id == task_id:
                return task
    raise KeyError(f"Task not found: {task_id}")


def _set_phase_status_for_task(progress: Progress, task_id: str, status: PhaseStatus) -> None:
    for phase in progress.phases:
        if any(task.id == task_id for task in phase.tasks):
            phase.status = status
            return


def _recalculate_phase_status(phase: PhaseProgress) -> None:
    if all(task.status in {TaskStatus.COMPLETED, TaskStatus.ABORT} for task in phase.tasks):
        phase.status = PhaseStatus.COMPLETED
        return
    if any(task.status in {TaskStatus.IN_PROGRESS, TaskStatus.FAILED} for task in phase.tasks):
        phase.status = PhaseStatus.IN_PROGRESS
        return
    phase.status = PhaseStatus.NOT_STARTED


def select_next_task(progress: Progress, max_retries: int = 3) -> TaskProgress | None:
    """Select the next task honoring retry-first policy and current phase preference.

    Tasks in ``IN_PROGRESS`` are intentionally excluded from selection. Callers should handle
    orphaned in-progress tasks before requesting the next task.
    """
    current_phase_id = progress.meta.current_phase

    def collect(predicate: Callable[[TaskProgress], bool]) -> list[TaskProgress]:
        in_current: list[TaskProgress] = []
        in_other: list[TaskProgress] = []
        for phase in progress.phases:
            for task in phase.tasks:
                if predicate(task):
                    if phase.id == current_phase_id:
                        in_current.append(task)
                    else:
                        in_other.append(task)
        return in_current + in_other

    failed_retryable = collect(
        lambda task: task.status == TaskStatus.FAILED and task.retries < max_retries
    )
    if failed_retryable:
        return failed_retryable[0]

    not_started = collect(lambda task: task.status == TaskStatus.NOT_STARTED)
    if not_started:
        return not_started[0]

    return None


def find_in_progress_tasks(progress: Progress) -> list[TaskProgress]:
    """Return all tasks currently locked in IN_PROGRESS."""
    return [
        task
        for phase in progress.phases
        for task in phase.tasks
        if task.status == TaskStatus.IN_PROGRESS
    ]


def recover_in_progress_task(progress: Progress, task_id: str, max_retries: int = 3) -> None:
    """Recover a task left in IN_PROGRESS after interruption.

    Recovery transitions ``IN_PROGRESS`` tasks to ``FAILED`` (or ``ABORT`` if retries already
    exhausted) while preserving retries and accumulated feedback.
    """
    task = find_task(progress, task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        raise ValueError(
            f"Illegal transition {task.status.value} -> failed/abort for task {task_id}"
        )

    if task.retries >= max_retries:
        task.status = TaskStatus.ABORT
    else:
        task.status = TaskStatus.FAILED

    for phase in progress.phases:
        if any(member.id == task_id for member in phase.tasks):
            _recalculate_phase_status(phase)
            return


def lock_task(progress: Progress, task_id: str) -> None:
    """Transition task into IN_PROGRESS from legal states only."""
    task = find_task(progress, task_id)
    if task.status not in {TaskStatus.NOT_STARTED, TaskStatus.FAILED}:
        raise ValueError(
            f"Illegal transition {task.status.value} -> in_progress for task {task_id}"
        )
    task.status = TaskStatus.IN_PROGRESS
    _set_phase_status_for_task(progress, task_id, PhaseStatus.IN_PROGRESS)


def complete_task(progress: Progress, task_id: str) -> None:
    """Mark an in-progress task as completed."""
    task = find_task(progress, task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        raise ValueError(f"Illegal transition {task.status.value} -> completed for task {task_id}")
    task.status = TaskStatus.COMPLETED

    for phase in progress.phases:
        if any(member.id == task_id for member in phase.tasks):
            if all(
                member.status in {TaskStatus.COMPLETED, TaskStatus.ABORT} for member in phase.tasks
            ):
                phase.status = PhaseStatus.COMPLETED
            else:
                phase.status = PhaseStatus.IN_PROGRESS
            return


def fail_task(
    progress: Progress, task_id: str, feedback: FeedbackEntry, max_retries: int = 3
) -> None:
    """Fail an in-progress task and append feedback, moving to ABORT when retries exhausted."""
    task = find_task(progress, task_id)
    if task.status != TaskStatus.IN_PROGRESS:
        raise ValueError(
            f"Illegal transition {task.status.value} -> failed/abort for task {task_id}"
        )

    task.retries += 1
    task.feedback.append(feedback)

    if task.retries >= max_retries:
        task.status = TaskStatus.ABORT
    else:
        task.status = TaskStatus.FAILED


def advance_phase(progress: Progress) -> bool:
    """Advance to the next phase when current is fully done/aborted."""
    current = get_current_phase(progress)
    if not all(task.status in {TaskStatus.COMPLETED, TaskStatus.ABORT} for task in current.tasks):
        return False

    sorted_phases = sorted(progress.phases, key=lambda phase: phase.id)
    for index, phase in enumerate(sorted_phases):
        if phase.id == current.id:
            if index + 1 < len(sorted_phases):
                progress.meta.current_phase = sorted_phases[index + 1].id
                return True
            return False
    return False
