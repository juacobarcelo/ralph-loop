from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import yaml

from ralph_loop.cli import main
from ralph_loop.config import RalphConfig
from ralph_loop.progress import load_progress, save_progress


def test_status_command(sample_workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["status", "--config", str(sample_workspace / "ralph-config.yaml")]
    )
    assert result.exit_code == 0
    assert "ralph-loop status" in result.output


def test_list_engines_command(sample_workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["list-engines", "--config", str(sample_workspace / "ralph-config.yaml")]
    )
    assert result.exit_code == 0
    assert "codex" in result.output
    assert "copilot" in result.output


def test_validate_command(sample_workspace: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["validate", "--config", str(sample_workspace / "ralph-config.yaml")]
    )
    assert result.exit_code == 0
    assert "Validation passed." in result.output


def test_init_command_generates_files_with_backend_output(
    sample_workspace: Path, monkeypatch
) -> None:
    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Demo Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [
                            {
                                "id": "01",
                                "title": "Create feature",
                                "description": "Implement feature",
                                "acceptance_criteria": ["It works", "It is tested"],
                                "test_plan": "1. Run tests",
                                "priority": "high",
                                "verify_commands": ["pytest tests/"],
                                "files_to_touch": ["src/feature.py"],
                                "files_not_to_touch": ["src/core.py"],
                                "constraints": ["Follow style"],
                                "reference_impl": None,
                            }
                        ],
                    }
                ],
            }

            class _Result:
                exit_code = 0
                stdout = json.dumps(payload)
                stderr = ""
                duration_seconds = 0.1
                timed_out = False

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _FakeBackend())

    plan_path = sample_workspace / "plan.md"
    plan_path.write_text("# Plan\n- [ ] Create feature", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--config",
            str(sample_workspace / "ralph-config.yaml"),
        ],
    )
    assert result.exit_code == 0

    generated_task = sample_workspace / "tasks" / "01-create-feature.md"
    assert generated_task.exists()

    progress_path = sample_workspace / "PROGRESS.yaml"
    payload = yaml.safe_load(progress_path.read_text(encoding="utf-8"))
    assert payload["meta"]["title"] == "Demo Plan"
    assert payload["phases"][0]["tasks"][0]["title"] == "Create feature"


def test_init_command_falls_back_when_backend_unavailable(
    sample_workspace: Path, monkeypatch
) -> None:
    class _UnavailableBackend:
        def is_available(self) -> bool:
            return False

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _UnavailableBackend())

    plan_path = sample_workspace / "plan-fallback.md"
    plan_path.write_text("# MVP\n- [ ] First task\n- [ ] Second task", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--config",
            str(sample_workspace / "ralph-config.yaml"),
        ],
    )
    assert result.exit_code == 0

    assert (sample_workspace / "tasks" / "01-first-task.md").exists()
    assert (sample_workspace / "tasks" / "02-second-task.md").exists()


def test_next_action_and_update_flow(sample_workspace: Path, monkeypatch) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"
    tmp_dir = sample_workspace / ".ralph-tmp"
    tmp_dir.mkdir(exist_ok=True)

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].verify_commands = ["pytest tests/test_dummy.py"]
    save_progress(progress, str(progress_path))

    config = RalphConfig.load(str(config_path))
    assert not Path(config.pause_file).exists()

    original_exists = Path.exists

    def _patched_exists(path: Path) -> bool:
        if path.resolve() == Path(config.pause_file).resolve():
            return False
        return original_exists(path)

    monkeypatch.setattr("ralph_loop.cli.Path.exists", _patched_exists)

    runner = CliRunner()

    first = runner.invoke(main, ["next-action", "--config", str(config_path)])
    assert first.exit_code == 0
    first_payload = json.loads(first.output)
    assert first_payload["command"] == "code", first_payload

    step_result = tmp_dir / "step-result.json"
    step_result.write_text(
        json.dumps({"step": "code", "task_id": "01", "exit_code": 0}),
        encoding="utf-8",
    )
    second = runner.invoke(
        main,
        [
            "next-action",
            "--config",
            str(config_path),
            "--step-result",
            str(step_result),
        ],
    )
    assert second.exit_code == 0
    assert json.loads(second.output)["command"] == "verify"

    step_result.write_text(
        json.dumps(
            {
                "step": "verify",
                "task_id": "01",
                "results": [
                    {"command": "pytest tests/test_dummy.py", "exit_code": 0, "stdout": "ok"}
                ],
            }
        ),
        encoding="utf-8",
    )
    third = runner.invoke(
        main,
        [
            "next-action",
            "--config",
            str(config_path),
            "--step-result",
            str(step_result),
        ],
    )
    assert third.exit_code == 0
    assert json.loads(third.output)["command"] == "inspect"

    step_result.write_text(
        json.dumps(
            {
                "step": "inspect",
                "task_id": "01",
                "stdout": json.dumps({"verdict": "pass", "feedback": "ok"}),
            }
        ),
        encoding="utf-8",
    )
    fourth = runner.invoke(
        main,
        [
            "next-action",
            "--config",
            str(config_path),
            "--step-result",
            str(step_result),
        ],
    )
    assert fourth.exit_code == 0
    assert json.loads(fourth.output)["command"] == "update"

    update_result = runner.invoke(
        main,
        [
            "update",
            "--config",
            str(config_path),
            "--result-dir",
            str(tmp_dir),
        ],
    )
    assert update_result.exit_code == 0

    updated = load_progress(str(progress_path))
    assert updated.phases[0].tasks[0].status.value == "completed"


def test_execute_command_uses_available_backend(tmp_path: Path, monkeypatch) -> None:
    prompt_path = tmp_path / "coder-prompt.md"
    prompt_path.write_text("implement", encoding="utf-8")

    class _Backend:
        def __init__(self, engine: str) -> None:
            self.engine = engine

        def is_available(self) -> bool:
            return self.engine == "copilot"

        def execute(
            self,
            prompt: str,
            model: str | None = None,
            timeout_seconds: int = 600,
            extra_flags: list[str] | None = None,
            cwd: str | None = None,
        ):
            _ = model, timeout_seconds, extra_flags, cwd

            class _Result:
                exit_code = 0
                stdout = f"ran:{prompt}"
                stderr = ""

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _Backend(engine))

    runner = CliRunner()
    result = runner.invoke(main, ["execute", "--prompt-file", str(prompt_path)])
    assert result.exit_code == 0
    assert f"ran:{prompt_path}" in result.output


def test_inspect_command_propagates_backend_exit_code(tmp_path: Path, monkeypatch) -> None:
    prompt_path = tmp_path / "inspector-prompt.md"
    prompt_path.write_text("inspect", encoding="utf-8")

    class _Backend:
        def __init__(self, engine: str) -> None:
            self.engine = engine

        def is_available(self) -> bool:
            return self.engine == "codex"

        def execute(
            self,
            prompt: str,
            model: str | None = None,
            timeout_seconds: int = 600,
            extra_flags: list[str] | None = None,
            cwd: str | None = None,
        ):
            _ = prompt, model, timeout_seconds, extra_flags, cwd

            class _Result:
                exit_code = 7
                stdout = ""
                stderr = "backend failed"

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _Backend(engine))

    runner = CliRunner()
    result = runner.invoke(main, ["inspect", "--prompt-file", str(prompt_path)])
    assert result.exit_code == 7


def test_run_command_delegates_to_loop(sample_workspace: Path, monkeypatch) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    captured: dict[str, object] = {}

    def _fake_run_loop(config, sandbox):
        captured["workspace"] = config.workspace_dir
        captured["sandbox"] = sandbox
        return 0

    monkeypatch.setattr("ralph_loop.cli.run_loop", _fake_run_loop)

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config_path), "--sandbox", "docker"])

    assert result.exit_code == 0
    assert captured["sandbox"] == "docker"


def test_run_command_returns_loop_exit_code(sample_workspace: Path, monkeypatch) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    monkeypatch.setattr("ralph_loop.cli.run_loop", lambda config, sandbox: 1)

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config_path), "--sandbox", "none"])

    assert result.exit_code == 1
