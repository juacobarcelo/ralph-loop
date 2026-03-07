from __future__ import annotations

from types import SimpleNamespace

from ralph_loop.config import RalphConfig, VisualVerifyConfig
from ralph_loop.progress import TaskProgress
from ralph_loop.task import Task
from ralph_loop.verification.pipeline import run_verification_pipeline
from ralph_loop.verification.visual import run_visual_verification


class _FakeInspectorBackend:
    def __init__(self, output: str) -> None:
        self.output = output

    @property
    def name(self) -> str:
        return "copilot"

    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 600,
        extra_flags: list[str] | None = None,
        cwd: str | None = None,
    ):
        _ = prompt, model, timeout_seconds, extra_flags, cwd
        return SimpleNamespace(exit_code=0, stdout=self.output, stderr="", timed_out=False)

    def is_available(self) -> bool:
        return True


def test_pipeline_runs_all_verifiers_even_when_deterministic_fails(monkeypatch, tmp_path) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text(
        """---
phase: 1
verify_commands: []
files_to_touch: []
files_not_to_touch: []
---

# Task 01

## Description

demo

## Acceptance Criteria

1. ok

## Test Plan

1. run
""",
        encoding="utf-8",
    )
    task = Task.load(str(task_file))
    task_progress = TaskProgress(id="01", title="Task 01", task_file=str(task_file))

    config = RalphConfig.model_validate(
        {
            "workspace_dir": str(tmp_path),
            "progress_file": str(tmp_path / "PROGRESS.yaml"),
            "task_dir": str(tmp_path),
            "backends": {
                "coder": {"engine": "codex"},
                "inspector": {"engine": "copilot", "timeout_seconds": 10},
            },
            "verify_commands": [],
        }
    )

    def _fake_run(command, cwd, capture_output, text, check):
        _ = cwd, capture_output, text, check
        if command[:3] == ["git", "--no-pager", "diff"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="failed", stderr="boom")

    monkeypatch.setattr("subprocess.run", _fake_run)

    report = run_verification_pipeline(
        task=task,
        task_progress=task_progress,
        config=config,
        inspector_backend=_FakeInspectorBackend('{"verdict":"pass","feedback":"ok"}'),
        verify_commands=["verify-cmd"],
    )

    assert report.all_passed is False
    assert any(source.type == "test" for source in report.feedback_sources)
    assert any(source.type == "ai_inspection" for source in report.feedback_sources)
    assert any(source.type == "visual" for source in report.feedback_sources)


def test_pipeline_handles_malformed_inspector_output(monkeypatch, tmp_path) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text(
        """---
phase: 1
verify_commands: []
files_to_touch: []
files_not_to_touch: []
---

# Task 01

## Description

demo

## Acceptance Criteria

1. ok

## Test Plan

1. run
""",
        encoding="utf-8",
    )
    task = Task.load(str(task_file))
    task_progress = TaskProgress(id="01", title="Task 01", task_file=str(task_file))

    config = RalphConfig.model_validate(
        {
            "workspace_dir": str(tmp_path),
            "progress_file": str(tmp_path / "PROGRESS.yaml"),
            "task_dir": str(tmp_path),
            "backends": {
                "coder": {"engine": "codex"},
                "inspector": {"engine": "copilot", "timeout_seconds": 10},
            },
            "verify_commands": [],
        }
    )

    def _fake_run(command, cwd, capture_output, text, check):
        _ = cwd, capture_output, text, check
        if command[:3] == ["git", "--no-pager", "diff"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    backend = _FakeInspectorBackend("not json")
    report = run_verification_pipeline(
        task=task,
        task_progress=task_progress,
        config=config,
        inspector_backend=backend,
        verify_commands=["verify-cmd"],
    )

    inspection_sources = [
        source for source in report.feedback_sources if source.type == "ai_inspection"
    ]
    assert inspection_sources
    assert inspection_sources[0].verdict == "fail"


def test_visual_verification_uses_llm_verdict(monkeypatch, tmp_path) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text(
        """---
phase: 1
verify_commands: []
files_to_touch: []
files_not_to_touch: []
---

# Task 01

## Description

demo
""",
        encoding="utf-8",
    )
    task = Task.load(str(task_file))

    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")
    screenshot = tmp_path / ".ralph-tmp" / "visual" / "current.png"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"current")

    monkeypatch.setattr(
        "ralph_loop.verification.visual._capture_screenshot",
        lambda config, workspace: SimpleNamespace(path=screenshot, error=None),
    )

    backend = _FakeInspectorBackend('{"verdict":"pass","feedback":"visual matches"}')
    result = run_visual_verification(
        config=VisualVerifyConfig(
            type="screenshot",
            url="http://localhost:3000",
            reference="reference.png",
            assertion="Homepage layout matches",
            viewport_width=1280,
            viewport_height=720,
        ),
        workspace_dir=str(tmp_path),
        backend=backend,
        model="claude-opus",
        timeout_seconds=60,
        extra_flags=[],
        task=task,
    )

    assert result.verdict == "pass"
    assert "visual matches" in result.details


def test_visual_verification_resolves_reference_from_workspace_ancestors(
    monkeypatch, tmp_path
) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text(
        """---
phase: 1
verify_commands: []
files_to_touch: []
files_not_to_touch: []
---

# Task 01

## Description

demo
""",
        encoding="utf-8",
    )
    task = Task.load(str(task_file))

    workspace_dir = tmp_path / "tmp" / "product"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    reference = workspace_dir / "references" / "mini-calc-home.jpg"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_bytes(b"reference")

    screenshot = workspace_dir / ".ralph-tmp" / "visual" / "current.jpg"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"current")

    monkeypatch.setattr(
        "ralph_loop.verification.visual._capture_screenshot",
        lambda config, workspace: SimpleNamespace(path=screenshot, error=None),
    )

    backend = _FakeInspectorBackend('{"verdict":"pass","feedback":"resolved"}')
    result = run_visual_verification(
        config=VisualVerifyConfig(
            type="screenshot",
            url="http://localhost:3000",
            reference="tmp/product/references/mini-calc-home.jpg",
            assertion="Homepage layout matches",
            viewport_width=1280,
            viewport_height=720,
        ),
        workspace_dir=str(workspace_dir),
        backend=backend,
        model="claude-opus",
        timeout_seconds=60,
        extra_flags=[],
        task=task,
    )

    assert result.verdict == "pass"
    assert "resolved" in result.details


def test_visual_verification_without_reference_uses_single_screenshot(
    monkeypatch, tmp_path
) -> None:
    task_file = tmp_path / "task.md"
    task_file.write_text(
        """---
phase: 1
verify_commands: []
files_to_touch: []
files_not_to_touch: []
---

# Task 01

## Description

demo
""",
        encoding="utf-8",
    )
    task = Task.load(str(task_file))

    screenshot = tmp_path / ".ralph-tmp" / "visual" / "current.jpg"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(b"current")

    monkeypatch.setattr(
        "ralph_loop.verification.visual._capture_screenshot",
        lambda config, workspace: SimpleNamespace(path=screenshot, error=None),
    )

    backend = _FakeInspectorBackend('{"verdict":"pass","feedback":"single screenshot ok"}')
    result = run_visual_verification(
        config=VisualVerifyConfig(
            type="screenshot",
            url="http://localhost:3000",
            reference=None,
            assertion="Page has a visible Mini Calc heading",
            viewport_width=1280,
            viewport_height=720,
        ),
        workspace_dir=str(tmp_path),
        backend=backend,
        model="claude-opus",
        timeout_seconds=60,
        extra_flags=[],
        task=task,
    )

    assert result.verdict == "pass"
    assert "single screenshot ok" in result.details


def test_visual_url_rewriting_in_container(monkeypatch, tmp_path) -> None:
    """Test that localhost URLs are rewritten to host.docker.internal inside containers."""
    from ralph_loop.verification.visual import (
        _normalize_visual_target_url,
        _rewrite_localhost_for_container,
    )

    # Direct rewrite function
    assert (
        _rewrite_localhost_for_container("http://localhost:8894")
        == "http://host.docker.internal:8894"
    )
    assert (
        _rewrite_localhost_for_container("http://localhost:3000/api")
        == "http://host.docker.internal:3000/api"
    )
    assert _rewrite_localhost_for_container("http://example.com:8080") == "http://example.com:8080"

    # Simulate container environment
    dockerenv = tmp_path / ".dockerenv"
    dockerenv.touch()
    monkeypatch.setattr(
        "ralph_loop.verification.visual._is_running_in_container",
        lambda: True,
    )

    result = _normalize_visual_target_url("http://localhost:8894", tmp_path)
    assert result == "http://host.docker.internal:8894"

    # Non-localhost URLs are not rewritten
    result = _normalize_visual_target_url("http://myservice:8080", tmp_path)
    assert result == "http://myservice:8080"


def test_visual_url_not_rewritten_outside_container(monkeypatch, tmp_path) -> None:
    """Test that localhost URLs are NOT rewritten when not in a container."""
    from ralph_loop.verification.visual import _normalize_visual_target_url

    monkeypatch.setattr(
        "ralph_loop.verification.visual._is_running_in_container",
        lambda: False,
    )

    # Outside container, localhost stays as-is
    result = _normalize_visual_target_url("http://localhost:8894", tmp_path)
    assert result == "http://localhost:8894"


def test_visual_verify_config_ignores_legacy_setup_teardown_commands() -> None:
    """Legacy setup/teardown keys should be ignored by the model."""
    config = VisualVerifyConfig(
        type="screenshot",
        url="http://localhost:8894",
        assertion="Page loads correctly",
        setup_commands=["docker compose up -d myservice", "sleep 3"],
        teardown_commands=["docker compose stop myservice"],
    )
    assert config.url == "http://localhost:8894"
    assert config.assertion == "Page loads correctly"


def test_visual_verify_config_defaults_without_setup_teardown() -> None:
    """Visual verification config no longer exposes setup/teardown commands."""
    config = VisualVerifyConfig(
        type="screenshot",
        url="http://localhost:8894",
        assertion="Page loads correctly",
    )
    assert config.model_dump() == {
        "type": "screenshot",
        "url": "http://localhost:8894",
        "reference": None,
        "assertion": "Page loads correctly",
        "viewport_width": 1280,
        "viewport_height": 720,
    }
