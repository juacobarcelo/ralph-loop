from __future__ import annotations

from pathlib import Path

from ralph_loop.progress import (
    FeedbackEntry,
    Progress,
    TaskStatus,
    advance_phase,
    complete_task,
    fail_task,
    load_progress,
    lock_task,
    save_progress,
    select_next_task,
)


def test_load_save_roundtrip(sample_workspace: Path) -> None:
    progress_path = sample_workspace / "PROGRESS.yaml"
    progress = load_progress(str(progress_path))
    save_progress(progress, str(progress_path))
    loaded = load_progress(str(progress_path))
    assert isinstance(loaded, Progress)
    assert loaded.meta.title == progress.meta.title


def test_select_next_task_prefers_not_started(sample_workspace: Path) -> None:
    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    task = select_next_task(progress)
    assert task is not None
    assert task.id == "01"


def test_lock_complete_task(sample_workspace: Path) -> None:
    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    lock_task(progress, "01")
    assert progress.phases[0].tasks[0].status == TaskStatus.IN_PROGRESS
    complete_task(progress, "01")
    assert progress.phases[0].tasks[0].status == TaskStatus.COMPLETED


def test_fail_task_and_abort(sample_workspace: Path) -> None:
    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    lock_task(progress, "01")
    fail_task(
        progress,
        "01",
        FeedbackEntry(attempt=1, timestamp="2026-03-01T00:00:00Z", sources=[]),
        max_retries=1,
    )
    assert progress.phases[0].tasks[0].status == TaskStatus.ABORT


def test_advance_phase_false_when_single_phase(sample_workspace: Path) -> None:
    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    lock_task(progress, "01")
    complete_task(progress, "01")
    assert advance_phase(progress) is False
