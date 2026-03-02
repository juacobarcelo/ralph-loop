from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import yaml

from ralph_loop import cli as cli_module
from ralph_loop.cli import main
from ralph_loop.config import RalphConfig, VisualVerifyConfig
from ralph_loop.progress import TaskStatus, load_progress, save_progress


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
                                "visual_verify": {
                                    "type": "screenshot",
                                    "url": "http://localhost:3000",
                                    "reference": "references/home.jpg",
                                    "assertion": "Main title is visible",
                                    "viewport_width": 1280,
                                    "viewport_height": 720,
                                },
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
    generated_task_content = generated_task.read_text(encoding="utf-8")
    assert "verify_commands:" in generated_task_content
    assert "visual_verify:" in generated_task_content
    assert "reference: references/home.jpg" in generated_task_content

    progress_path = sample_workspace / "PROGRESS.yaml"
    payload = yaml.safe_load(progress_path.read_text(encoding="utf-8"))
    assert payload["meta"]["title"] == "Demo Plan"
    assert payload["phases"][0]["tasks"][0]["title"] == "Create feature"
    assert payload["phases"][0]["tasks"][0]["verify_commands"] == ["pytest tests/"]
    assert payload["phases"][0]["tasks"][0]["visual_verify"]["reference"] == "references/home.jpg"


def test_init_command_fails_when_backend_unavailable(sample_workspace: Path, monkeypatch) -> None:
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
    assert result.exit_code != 0
    assert "requires AI generation" in result.output


def test_init_command_fails_when_backend_output_is_invalid(
    sample_workspace: Path, monkeypatch
) -> None:
    class _InvalidBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd

            class _Result:
                exit_code = 0
                stdout = "not-json"
                stderr = ""
                duration_seconds = 0.1
                timed_out = False

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _InvalidBackend())

    plan_path = sample_workspace / "plan-invalid-output.md"
    plan_path.write_text("# Plan\n- [ ] First task", encoding="utf-8")

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

    assert result.exit_code != 0
    assert "requires AI generation" in result.output


def test_init_applies_verify_and_visual_hints(sample_workspace: Path, monkeypatch) -> None:
    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Visual Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [
                            {
                                "id": "01",
                                "title": "Build homepage",
                                "description": "Build homepage",
                            },
                            {"id": "02", "title": "Add tests", "description": "Add tests"},
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

    plan_path = sample_workspace / "plan-visual.md"
    plan_path.write_text(
        """# Plan

- [ ] Build homepage
- [ ] Add tests

Visual verification requirement:
- Add one task with `visual_verify` configured.
- Use a data URL as target (no local server required).
- Reference image path: `tmp/product/references/home.jpg`.
- Assertion: homepage title is visible.

Verification:
`python -m pytest -q ./tmp/product/tests`
""",
        encoding="utf-8",
    )

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

    progress = yaml.safe_load((sample_workspace / "PROGRESS.yaml").read_text(encoding="utf-8"))
    tasks = progress["phases"][0]["tasks"]
    assert all(
        task["verify_commands"] == ["python -m pytest -q ./tmp/product/tests"] for task in tasks
    )
    assert any(task["visual_verify"] is not None for task in tasks)


def test_init_applies_visual_hint_without_reference(sample_workspace: Path, monkeypatch) -> None:
    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Visual Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [
                            {"id": "01", "title": "Build homepage", "description": "Build homepage"}
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

    plan_path = sample_workspace / "plan-visual-no-reference.md"
    plan_path.write_text(
        """# Plan

- [ ] Build homepage

Visual verification requirement:
- Add one task with `visual_verify` configured.
- Use a data URL as target (no local server required).
- Do not use any reference image; inspect the current screenshot only.
- Assertion: homepage title is visible.
""",
        encoding="utf-8",
    )

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

    progress = yaml.safe_load((sample_workspace / "PROGRESS.yaml").read_text(encoding="utf-8"))
    tasks = progress["phases"][0]["tasks"]
    visual_tasks = [task for task in tasks if task["visual_verify"] is not None]
    assert visual_tasks
    assert visual_tasks[0]["visual_verify"]["reference"] is None


def test_init_includes_inline_instructions_in_prompt(sample_workspace: Path, monkeypatch) -> None:
    captured_prompt: dict[str, str] = {}

    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = model, timeout_seconds, extra_flags, cwd
            captured_prompt["value"] = prompt
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

    plan_path = sample_workspace / "plan-inline-directives.md"
    plan_path.write_text("# Plan\n- [ ] Create feature", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--instructions",
            "Review visually that the video list renders correctly.",
            "--config",
            str(sample_workspace / "ralph-config.yaml"),
        ],
    )

    assert result.exit_code == 0
    assert "Additional directives (highest priority):" in captured_prompt["value"]
    assert "Review visually that the video list renders correctly." in captured_prompt["value"]


def test_init_includes_instructions_file_and_inline_in_prompt(
    sample_workspace: Path, monkeypatch
) -> None:
    captured_prompt: dict[str, str] = {}

    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = model, timeout_seconds, extra_flags, cwd
            captured_prompt["value"] = prompt
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

    plan_path = sample_workspace / "plan-file-directives.md"
    plan_path.write_text("# Plan\n- [ ] Create feature", encoding="utf-8")

    directives_path = sample_workspace / "directives.md"
    directives_path.write_text(
        "Check only at the end visually that all buttons are legible.",
        encoding="utf-8",
    )

    inline_directive = "Review visually that the video list renders correctly."
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--instructions-file",
            str(directives_path),
            "--instructions",
            inline_directive,
            "--config",
            str(sample_workspace / "ralph-config.yaml"),
        ],
    )

    assert result.exit_code == 0
    prompt_value = captured_prompt["value"]
    assert "Check only at the end visually that all buttons are legible." in prompt_value
    assert inline_directive in prompt_value
    assert prompt_value.index(
        "Check only at the end visually that all buttons are legible."
    ) < prompt_value.index(inline_directive)


def test_init_visual_directive_only_at_end(sample_workspace: Path, monkeypatch) -> None:
    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Directive Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [
                            {
                                "id": "01",
                                "title": "Build homepage layout",
                                "description": "Build homepage layout",
                            },
                            {
                                "id": "02",
                                "title": "Improve homepage button readability",
                                "description": "Improve homepage button readability",
                            },
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

    plan_path = sample_workspace / "plan-visual-end-only.md"
    plan_path.write_text(
        """# Plan

- [ ] Build homepage layout
- [ ] Improve homepage button readability
""",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--instructions",
            "Revisar solo al final visualmente que todos los botones estén desplegados y sean legibles.",
            "--config",
            str(sample_workspace / "ralph-config.yaml"),
        ],
    )

    assert result.exit_code == 0
    progress = yaml.safe_load((sample_workspace / "PROGRESS.yaml").read_text(encoding="utf-8"))
    tasks = progress["phases"][0]["tasks"]
    visual_tasks = [task for task in tasks if task["visual_verify"] is not None]
    assert len(visual_tasks) == 1
    assert visual_tasks[0]["id"] == "02"


def test_init_visual_directive_targets_specific_task(sample_workspace: Path, monkeypatch) -> None:
    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Directive Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [
                            {
                                "id": "01",
                                "title": "Crear API de videos",
                                "description": "Crear API de videos",
                            },
                            {
                                "id": "02",
                                "title": "Renderizar lista de videos",
                                "description": "Renderizar lista de videos",
                            },
                            {
                                "id": "03",
                                "title": "Añadir tests",
                                "description": "Añadir tests",
                            },
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

    plan_path = sample_workspace / "plan-visual-targeted.md"
    plan_path.write_text(
        """# Plan

- [ ] Crear API de videos
- [ ] Renderizar lista de videos
- [ ] Añadir tests
""",
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--instructions",
            "Revisar visualmente que la lista de videos se obtiene correctamente.",
            "--config",
            str(sample_workspace / "ralph-config.yaml"),
        ],
    )

    assert result.exit_code == 0
    progress = yaml.safe_load((sample_workspace / "PROGRESS.yaml").read_text(encoding="utf-8"))
    tasks = progress["phases"][0]["tasks"]
    visual_tasks = [task for task in tasks if task["visual_verify"] is not None]
    assert len(visual_tasks) == 1
    assert visual_tasks[0]["id"] == "02"


def test_init_creates_target_directories_when_missing(sample_workspace: Path, monkeypatch) -> None:
    config_path = sample_workspace / "ralph-config.missing-dirs.yaml"
    workspace_dir = sample_workspace / "generated" / "workspace"
    task_dir = sample_workspace / "generated" / "tasks"
    progress_file = sample_workspace / "generated" / "state" / "PROGRESS.yaml"
    pause_file = sample_workspace / "generated" / "state" / "PAUSE.md"

    config_payload = yaml.safe_load(
        (sample_workspace / "ralph-config.yaml").read_text(encoding="utf-8")
    )
    config_payload["workspace_dir"] = str(workspace_dir)
    config_payload["task_dir"] = str(task_dir)
    config_payload["progress_file"] = str(progress_file)
    config_payload["pause_file"] = str(pause_file)
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Setup Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [{"id": "01", "title": "Setup project"}],
                    }
                ],
            }

            class _Result:
                exit_code = 0
                stdout = json.dumps(payload)
                stderr = ""

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _FakeBackend())

    plan_path = sample_workspace / "plan-missing-dirs.md"
    plan_path.write_text("# Plan\n- [ ] Setup project", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--config",
            str(config_path),
        ],
    )
    assert result.exit_code == 0
    assert workspace_dir.exists()
    assert task_dir.exists()
    assert progress_file.parent.exists()
    assert pause_file.parent.exists()
    assert progress_file.exists()


def test_init_uses_derived_loop_directory_with_global_config(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    config_path = tmp_path / "global-config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "max_retries": 3,
                "backends": {
                    "coder": {"engine": "codex", "timeout_seconds": 60},
                    "inspector": {"engine": "copilot", "timeout_seconds": 60},
                },
                "verify_commands": [],
                "auth": {},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    class _FakeBackend:
        def is_available(self) -> bool:
            return True

        def execute(self, prompt: str, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
            _ = prompt, model, timeout_seconds, extra_flags, cwd
            payload = {
                "title": "Setup Plan",
                "phases": [
                    {
                        "id": 1,
                        "name": "Phase 1",
                        "tasks": [{"id": "01", "title": "Setup project"}],
                    }
                ],
            }

            class _Result:
                exit_code = 0
                stdout = json.dumps(payload)
                stderr = ""

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _FakeBackend())

    plan_path = workspace / "my-feature-plan.md"
    plan_path.write_text("# Plan\n- [ ] Setup project", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--from",
            str(plan_path),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0
    loop_dir = workspace / ".ralph-loop" / "my-feature-plan"
    assert (loop_dir / "tasks" / "01-setup-project.md").exists()
    assert (loop_dir / "PROGRESS.yaml").exists()
    assert (loop_dir / "product").exists()


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
    second_payload = json.loads(second.output)
    assert second_payload["command"] == "verify"
    assert second_payload["workspace_dir"] == config.workspace_dir

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
    captured: dict[str, object] = {}

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
            captured["prompt"] = prompt
            captured["model"] = model
            captured["timeout_seconds"] = timeout_seconds
            captured["extra_flags"] = extra_flags
            captured["cwd"] = cwd

            class _Result:
                exit_code = 0
                stdout = f"ran:{prompt}"
                stderr = ""

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _Backend(engine))

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "execute",
            "--prompt-file",
            str(prompt_path),
            "--model",
            "gpt-x",
            "--timeout-seconds",
            "42",
            "--extra-flag",
            "flag-1",
        ],
    )
    assert result.exit_code == 0
    assert "ran:implement" in result.output
    assert captured["prompt"] == "implement"
    assert captured["model"] == "gpt-x"
    assert captured["timeout_seconds"] == 42
    assert captured["extra_flags"] == ["flag-1"]
    assert captured["cwd"] == str(prompt_path.parent)


def test_execute_command_uses_workspace_cwd_for_ralph_tmp_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    prompt_dir = tmp_path / ".ralph-tmp"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / "coder-prompt.md"
    prompt_path.write_text("implement", encoding="utf-8")
    captured: dict[str, object] = {}

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
            _ = prompt, model, timeout_seconds, extra_flags
            captured["cwd"] = cwd

            class _Result:
                exit_code = 0
                stdout = "ok"
                stderr = ""

            return _Result()

    monkeypatch.setattr("ralph_loop.cli.get_backend", lambda engine: _Backend(engine))

    runner = CliRunner()
    result = runner.invoke(main, ["execute", "--prompt-file", str(prompt_path)])
    assert result.exit_code == 0
    assert captured["cwd"] == str(tmp_path)


def test_parse_inspector_output_extracts_json_from_mixed_output() -> None:
    mixed_output = """
notes before json
```json
{"verdict":"pass","feedback":"all good"}
```
"""
    verdict, feedback = cli_module._parse_inspector_output(mixed_output)
    assert verdict == "pass"
    assert feedback == "all good"


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


def test_resolve_task_file_path_falls_back_to_task_dir(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config = RalphConfig.load(str(config_path))
    task_file = sample_workspace / "tasks" / "01-task.md"

    resolved = cli_module._resolve_task_file_path(
        f"/workspace/tmp/{task_file.name}",
        config,
    )

    assert resolved == task_file.resolve()


def test_build_coder_prompt_uses_fallback_task_resolution(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config = RalphConfig.load(str(config_path))
    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    task = progress.phases[0].tasks[0]
    task.task_file = f"/workspace/tmp/{Path(task.task_file).name}"

    prompt = cli_module._build_coder_prompt(task, config)

    assert prompt.strip() == "# task"


def test_next_action_includes_visual_step(sample_workspace: Path, monkeypatch) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"
    tmp_dir = sample_workspace / ".ralph-tmp"
    tmp_dir.mkdir(exist_ok=True)

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].verify_commands = []
    progress.phases[0].tasks[0].visual_verify = VisualVerifyConfig.model_validate(
        {
            "type": "screenshot",
            "url": "http://localhost:3000",
            "reference": "references/home.png",
            "assertion": "Layout should match",
            "viewport_width": 1280,
            "viewport_height": 720,
        }
    )
    save_progress(progress, str(progress_path))

    config = RalphConfig.load(str(config_path))
    original_exists = Path.exists

    def _patched_exists(path: Path) -> bool:
        if path.resolve() == Path(config.pause_file).resolve():
            return False
        return original_exists(path)

    monkeypatch.setattr("ralph_loop.cli.Path.exists", _patched_exists)

    runner = CliRunner()
    first = runner.invoke(main, ["next-action", "--config", str(config_path)])
    assert first.exit_code == 0
    assert json.loads(first.output)["command"] == "code"

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
    second_payload = json.loads(second.output)
    assert second_payload["command"] == "visual"
    assert "image" in second_payload

    step_result.write_text(
        json.dumps(
            {
                "step": "visual",
                "task_id": "01",
                "exit_code": 0,
                "stdout": json.dumps({"verdict": "pass", "feedback": "ok"}),
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


def test_next_action_returns_abort_when_any_task_is_aborted(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].status = TaskStatus.ABORT
    save_progress(progress, str(progress_path))

    config = RalphConfig.load(str(config_path))
    original_exists = Path.exists

    def _patched_exists(path: Path) -> bool:
        if path.resolve() == Path(config.pause_file).resolve():
            return False
        return original_exists(path)

    monkeypatch.setattr("ralph_loop.cli.Path.exists", _patched_exists)

    runner = CliRunner()
    result = runner.invoke(main, ["next-action", "--config", str(config_path)])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "abort"
    assert payload["task_id"] == "01"


def test_update_command_fails_task_when_visual_step_fails(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config = RalphConfig.load(str(config_path))
    paths = sample_workspace / ".ralph-tmp"
    paths.mkdir(exist_ok=True)
    progress = load_progress(config.progress_file)
    progress.phases[0].tasks[0].status = TaskStatus.IN_PROGRESS
    save_progress(progress, config.progress_file)

    iteration = {
        "task_id": "01",
        "current_step": "update",
        "started_at": "2026-03-01T00:00:00Z",
        "results": [
            {"step": "code", "task_id": "01", "exit_code": 0},
            {
                "step": "visual",
                "task_id": "01",
                "exit_code": 0,
                "stdout": json.dumps({"verdict": "fail", "feedback": "mismatch"}),
            },
            {
                "step": "inspect",
                "task_id": "01",
                "exit_code": 0,
                "stdout": json.dumps({"verdict": "pass", "feedback": "ok"}),
            },
        ],
    }
    (sample_workspace / ".ralph-tmp" / "iteration-state.json").write_text(
        json.dumps(iteration),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["update", "--config", str(config_path), "--result-dir", str(paths)],
    )
    assert result.exit_code == 0

    progress = load_progress(config.progress_file)
    task = progress.phases[0].tasks[0]
    assert task.status.value == "failed"
    assert task.feedback
    assert any(source.type == "visual" for source in task.feedback[-1].sources)


def test_update_command_fails_when_visual_result_is_missing(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config = RalphConfig.load(str(config_path))
    paths = sample_workspace / ".ralph-tmp"
    paths.mkdir(exist_ok=True)
    progress = load_progress(config.progress_file)
    progress.phases[0].tasks[0].status = TaskStatus.IN_PROGRESS
    progress.phases[0].tasks[0].visual_verify = VisualVerifyConfig.model_validate(
        {
            "type": "screenshot",
            "url": "http://localhost:3000",
            "reference": None,
            "assertion": "Page must be valid",
            "viewport_width": 1280,
            "viewport_height": 720,
        }
    )
    save_progress(progress, config.progress_file)

    iteration = {
        "task_id": "01",
        "current_step": "update",
        "started_at": "2026-03-01T00:00:00Z",
        "results": [
            {"step": "code", "task_id": "01", "exit_code": 0},
            {
                "step": "inspect",
                "task_id": "01",
                "exit_code": 0,
                "stdout": json.dumps({"verdict": "pass", "feedback": "ok"}),
            },
        ],
    }
    (sample_workspace / ".ralph-tmp" / "iteration-state.json").write_text(
        json.dumps(iteration),
        encoding="utf-8",
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["update", "--config", str(config_path), "--result-dir", str(paths)],
    )
    assert result.exit_code == 0

    progress = load_progress(config.progress_file)
    task = progress.phases[0].tasks[0]
    assert task.status.value == "failed"
    assert task.feedback
    assert any(
        source.type == "visual"
        and source.verdict == "fail"
        and source.details == "Visual verification was required but no visual result was recorded."
        for source in task.feedback[-1].sources
    )


def test_build_inspector_prompt_includes_failure_gates() -> None:
    class _Task:
        id = "01"
        title = "Demo"

    prompt = cli_module._build_inspector_prompt(_Task(), [{"step": "visual"}])

    assert "If any deterministic verification failed, verdict MUST be `fail`." in prompt
    assert "If visual verification failed or could not run, verdict MUST be `fail`." in prompt


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


# ---------------------------------------------------------------------------
# Config resolution via CLI (no --config flag)
# ---------------------------------------------------------------------------


def test_cli_uses_local_config_when_no_flag(sample_workspace: Path, monkeypatch) -> None:
    """CLI picks up ralph-config.yaml from CWD when --config is omitted."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)
    monkeypatch.chdir(sample_workspace)
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: None)

    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code == 0
    assert "ralph-loop status" in result.output


def test_cli_error_when_no_config_found(tmp_path: Path, monkeypatch) -> None:
    """CLI shows clear error with attempted paths when no config exists."""
    monkeypatch.delenv("RALPH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("ralph_loop.config._get_repo_root", lambda: None)

    fake_global = tmp_path / "nope" / "config.yaml"
    monkeypatch.setattr("ralph_loop.config.GLOBAL_CONFIG_PATH", Path(str(fake_global)))

    runner = CliRunner()
    result = runner.invoke(main, ["status"])
    assert result.exit_code != 0
    assert "No ralph-loop config file found" in result.output
