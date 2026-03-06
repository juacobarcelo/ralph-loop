from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest
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


def test_init_engine_prefers_init_role(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["backends"]["init"] = {
        "engine": "claude",
        "model": "claude-opus-4-6",
        "timeout_seconds": 120,
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["init-engine", "--config", str(config_path)])

    assert result.exit_code == 0
    assert result.output.strip() == "claude"


def test_init_engine_accepts_initialize_alias(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["backends"]["initialize"] = {
        "engine": "claude",
        "model": "claude-opus-4-6",
        "timeout_seconds": 120,
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["init-engine", "--config", str(config_path)])

    assert result.exit_code == 0
    assert result.output.strip() == "claude"


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

    generated_task = sample_workspace / "tasks" / "01-create-feature.json"
    assert generated_task.exists()
    generated_task_payload = json.loads(generated_task.read_text(encoding="utf-8"))
    assert generated_task_payload["verify"]["commands"] == ["pytest tests/"]
    assert generated_task_payload["visual"]["reference"] == "references/home.jpg"
    assert generated_task_payload["coding"]["files_to_touch"] == ["src/feature.py"]

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
    assert "after 3 attempts" in result.output


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
    assert "after 3 attempts" in result.output


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


def test_init_ensures_unified_config_defaults(sample_workspace: Path, monkeypatch) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload.pop("review_mode", None)
    payload["backends"].pop("reviewer", None)
    payload.pop("runtime_guards", None)
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

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

    plan_path = sample_workspace / "plan-unified-defaults.md"
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

    updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert updated["review_mode"] == "unified_agent"
    assert "reviewer" in updated["backends"]
    assert updated["backends"]["reviewer"]["engine"] == updated["backends"]["inspector"]["engine"]
    assert updated["runtime_guards"]["pre_code"]["command"] == "./scripts/ralph/guard.sh"
    assert updated["runtime_guards"]["pre_code"]["on_failure"] == "pause_loop"
    assert updated["runtime_guards"]["post_code"]["on_failure"] == "fail_attempt"


def test_init_creates_guard_script_and_verify_file_in_workspace(
    sample_workspace: Path, monkeypatch
) -> None:
    script_path = sample_workspace / "scripts" / "ralph" / "guard.sh"
    verify_file = sample_workspace / "scripts" / "ralph" / "verify-commands.txt"
    assert not script_path.exists()
    assert not verify_file.exists()

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

    plan_path = sample_workspace / "plan-guard.md"
    plan_path.write_text("# Plan\n- [ ] Setup project", encoding="utf-8")

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
    assert script_path.exists()
    assert verify_file.exists()
    assert (script_path.stat().st_mode & 0o111) != 0
    content = script_path.read_text(encoding="utf-8")
    assert "docker compose up -d" in content
    assert "run_verify_checks" in content
    verify_content = verify_file.read_text(encoding="utf-8")
    assert "pnpm exec tsc --noEmit" in verify_content


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
    loop_dir = workspace / ".ralph-loop"
    assert (loop_dir / "tasks" / "01-setup-project.json").exists()
    assert (loop_dir / "PROGRESS.yaml").exists()
    assert (loop_dir / "product").exists()
    assert (loop_dir / "product" / "scripts" / "ralph" / "guard.sh").exists()


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


def test_strip_codex_reasoning_override_handles_both_config_forms() -> None:
    cleaned = cli_module._strip_codex_reasoning_override(
        [
            "--search",
            '--config=model_reasoning_effort="low"',
            "-c",
            'model_reasoning_effort="medium"',
            "-c",
            "features.foo=true",
        ]
    )
    assert cleaned == ["--search", "-c", "features.foo=true"]


def test_next_action_code_uses_high_reasoning_on_first_attempt(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["backends"]["coder"]["extra_flags"] = [
        "--search",
        '--config=model_reasoning_effort="low"',
    ]
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

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
    assert payload["command"] == "code"
    assert payload["extra_flags"] == [
        "--search",
        '--config=model_reasoning_effort="high"',
    ]


def test_next_action_code_uses_xhigh_reasoning_on_retry(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"
    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].status = TaskStatus.FAILED
    progress.phases[0].tasks[0].retries = 1
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
    assert payload["command"] == "code"
    assert payload["extra_flags"] == ['--config=model_reasoning_effort="xhigh"']


def test_next_action_inspect_uses_high_reasoning_when_engine_is_codex(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"
    tmp_dir = sample_workspace / ".ralph-tmp"
    tmp_dir.mkdir(exist_ok=True)

    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_payload["backends"]["inspector"] = {
        "engine": "codex",
        "model": "gpt-5.3-codex",
        "timeout_seconds": 300,
        "extra_flags": ["--search"],
    }
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].verify_commands = []
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
    payload = json.loads(second.output)
    assert payload["command"] == "inspect"
    assert payload["extra_flags"] == [
        "--search",
        '--config=model_reasoning_effort="high"',
    ]


def test_next_action_visual_uses_high_reasoning_when_engine_is_codex(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"
    tmp_dir = sample_workspace / ".ralph-tmp"
    tmp_dir.mkdir(exist_ok=True)

    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_payload["backends"]["visual"] = {
        "engine": "codex",
        "model": "gpt-5.3-codex",
        "timeout_seconds": 300,
        "extra_flags": ["--search"],
    }
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

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
    payload = json.loads(second.output)
    assert payload["command"] == "visual"
    assert payload["extra_flags"] == [
        "--search",
        '--config=model_reasoning_effort="high"',
    ]


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


def test_build_coder_prompt_includes_task_capability_instructions(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_payload["agent_capabilities"] = {
        "playwright": {
            "type": "builtin",
            "instruction": "You can run Playwright checks for UI validation.",
        }
    }
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    task_file = sample_workspace / "tasks" / "01-capabilities.json"
    task_file.write_text(
        json.dumps(
            {
                "id": "01",
                "title": "Capability prompt test",
                "phase": 1,
                "coding": {
                    "description": "Validate capability instructions in coder prompt.",
                    "acceptance_criteria": ["Prompt includes capability guidance."],
                    "files_to_touch": [],
                    "files_not_to_touch": [],
                    "constraints": [],
                    "reference_impl": None,
                },
                "verify": {"commands": []},
                "inspect": {
                    "acceptance_criteria": [],
                    "description_summary": "Capability prompt test",
                },
                "agent_capabilities": {"code": ["playwright"], "review": []},
            }
        ),
        encoding="utf-8",
    )

    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    progress.phases[0].tasks[0].task_file = str(task_file)
    save_progress(progress, str(sample_workspace / "PROGRESS.yaml"))

    config = RalphConfig.load(str(config_path))
    task = load_progress(str(sample_workspace / "PROGRESS.yaml")).phases[0].tasks[0]
    prompt = cli_module._build_coder_prompt(task, config)

    assert "## Available Capabilities" in prompt
    assert "`playwright` (builtin)" in prompt
    assert "Playwright checks for UI validation" in prompt


def test_emit_code_action_includes_capability_flags(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_payload["agent_capabilities"] = {
        "chrome-devtools": {
            "type": "mcp",
            "instruction": "Use Chrome MCP when browser inspection is required.",
            "backend_flags": {
                "codex": {"code": ["--config", "mcp_servers.chrome-devtools=enabled"]}
            },
        }
    }
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    task_file = sample_workspace / "tasks" / "01-capabilities.json"
    task_file.write_text(
        json.dumps(
            {
                "id": "01",
                "title": "Code action capabilities",
                "phase": 1,
                "coding": {
                    "description": "Ensure capability flags are propagated for code.",
                    "acceptance_criteria": ["Code action includes capability flags."],
                    "files_to_touch": [],
                    "files_not_to_touch": [],
                    "constraints": [],
                    "reference_impl": None,
                },
                "verify": {"commands": []},
                "inspect": {"acceptance_criteria": [], "description_summary": "Code action"},
                "agent_capabilities": {"code": ["chrome-devtools"], "review": []},
            }
        ),
        encoding="utf-8",
    )

    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    progress.phases[0].tasks[0].task_file = str(task_file)
    save_progress(progress, str(sample_workspace / "PROGRESS.yaml"))

    config = RalphConfig.load(str(config_path))
    task = load_progress(str(sample_workspace / "PROGRESS.yaml")).phases[0].tasks[0]
    paths = cli_module._runtime_paths(config)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)

    action = cli_module._emit_code_action(task=task, config=config, paths=paths)

    assert action["command"] == "code"
    assert action["capabilities"] == ["chrome-devtools"]
    assert "--config" in action["extra_flags"]
    assert "mcp_servers.chrome-devtools=enabled" in action["extra_flags"]
    assert '--config=model_reasoning_effort="high"' in action["extra_flags"]


def test_emit_review_action_includes_capability_flags_and_prompt_context(
    sample_workspace: Path,
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_payload["review_mode"] = "unified_agent"
    config_payload["backends"]["reviewer"] = {
        "engine": "codex",
        "model": "gpt-5.4-codex",
        "timeout_seconds": 300,
    }
    config_payload["agent_capabilities"] = {
        "chrome-devtools": {
            "type": "mcp",
            "instruction": "Use Chrome MCP to inspect UI state when acceptance requires it.",
            "backend_flags": {
                "codex": {"review": ["--config", "mcp_servers.chrome-devtools=enabled"]}
            },
        }
    }
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")

    task_file = sample_workspace / "tasks" / "01-capabilities.json"
    task_file.write_text(
        json.dumps(
            {
                "id": "01",
                "title": "Review action capabilities",
                "phase": 1,
                "coding": {
                    "description": "Ensure reviewer capability setup is propagated.",
                    "acceptance_criteria": ["Review action includes capability flags."],
                    "files_to_touch": [],
                    "files_not_to_touch": [],
                    "constraints": [],
                    "reference_impl": None,
                },
                "verify": {"commands": []},
                "inspect": {
                    "acceptance_criteria": ["Review action includes capability flags."],
                    "description_summary": "Review action capabilities",
                },
                "agent_capabilities": {"code": [], "review": ["chrome-devtools"]},
            }
        ),
        encoding="utf-8",
    )

    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    progress.phases[0].tasks[0].task_file = str(task_file)
    save_progress(progress, str(sample_workspace / "PROGRESS.yaml"))

    config = RalphConfig.load(str(config_path))
    task = load_progress(str(sample_workspace / "PROGRESS.yaml")).phases[0].tasks[0]
    paths = cli_module._runtime_paths(config)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)

    action = cli_module._emit_review_action(
        task=task,
        config=config,
        paths=paths,
        iteration={"task_id": "01", "results": []},
    )

    prompt = paths.reviewer_prompt_path.read_text(encoding="utf-8")
    assert "## Available Capabilities" in prompt
    assert "`chrome-devtools` (mcp)" in prompt
    assert action["command"] == "review"
    assert action["capabilities"] == ["chrome-devtools"]
    assert "--config" in action["extra_flags"]
    assert "mcp_servers.chrome-devtools=enabled" in action["extra_flags"]


def test_emit_code_action_fails_when_task_references_unknown_capability(
    sample_workspace: Path,
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    task_file = sample_workspace / "tasks" / "01-capabilities.json"
    task_file.write_text(
        json.dumps(
            {
                "id": "01",
                "title": "Unknown capability",
                "phase": 1,
                "coding": {
                    "description": "Task references capability missing from config.",
                    "acceptance_criteria": ["Should fail early."],
                    "files_to_touch": [],
                    "files_not_to_touch": [],
                    "constraints": [],
                    "reference_impl": None,
                },
                "verify": {"commands": []},
                "inspect": {"acceptance_criteria": [], "description_summary": "Unknown capability"},
                "agent_capabilities": {"code": ["missing-capability"], "review": []},
            }
        ),
        encoding="utf-8",
    )

    progress = load_progress(str(sample_workspace / "PROGRESS.yaml"))
    progress.phases[0].tasks[0].task_file = str(task_file)
    save_progress(progress, str(sample_workspace / "PROGRESS.yaml"))

    config = RalphConfig.load(str(config_path))
    task = load_progress(str(sample_workspace / "PROGRESS.yaml")).phases[0].tasks[0]
    paths = cli_module._runtime_paths(config)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(Exception):
        cli_module._emit_code_action(task=task, config=config, paths=paths)


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


def test_next_action_visual_includes_setup_teardown_commands(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"
    tmp_dir = sample_workspace / ".ralph-tmp"
    tmp_dir.mkdir(exist_ok=True)

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].verify_commands = []
    progress.phases[0].tasks[0].visual_verify = VisualVerifyConfig.model_validate(
        {
            "type": "screenshot",
            "url": "http://localhost:8894",
            "reference": None,
            "assertion": "Layout should match",
            "viewport_width": 1280,
            "viewport_height": 720,
            "setup_commands": ["docker compose up -d myservice", "sleep 3"],
            "teardown_commands": ["docker compose stop myservice"],
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
    assert second_payload["setup_commands"] == ["docker compose up -d myservice", "sleep 3"]
    assert second_payload["teardown_commands"] == ["docker compose stop myservice"]
    assert "workspace_dir" in second_payload


def test_next_action_unified_starts_with_runtime_pre_code(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["review_mode"] = "unified_agent"
    payload["runtime_guards"] = {
        "pre_code": {
            "command": "./scripts/ralph/guard.sh",
            "timeout_seconds": 120,
            "on_failure": "pause_loop",
        }
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

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
    output = json.loads(result.output)
    assert output["command"] == "runtime"
    assert output["phase"] == "pre_code"
    assert output["runtime_command"] == "./scripts/ralph/guard.sh"
    assert output["timeout_seconds"] == 120


def test_next_action_unified_transitions_runtime_code_runtime_review(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["review_mode"] = "unified_agent"
    payload["backends"]["reviewer"] = {
        "engine": "codex",
        "model": "gpt-5.4-codex",
        "timeout_seconds": 300,
    }
    payload["runtime_guards"] = {
        "pre_code": {
            "command": "./scripts/ralph/guard.sh",
            "timeout_seconds": 120,
            "on_failure": "pause_loop",
        },
        "post_code": {
            "command": "./scripts/ralph/guard.sh",
            "timeout_seconds": 120,
            "on_failure": "fail_attempt",
        },
    }
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    config = RalphConfig.load(str(config_path))
    original_exists = Path.exists

    def _patched_exists(path: Path) -> bool:
        if path.resolve() == Path(config.pause_file).resolve():
            return False
        return original_exists(path)

    monkeypatch.setattr("ralph_loop.cli.Path.exists", _patched_exists)

    tmp_dir = sample_workspace / ".ralph-tmp"
    tmp_dir.mkdir(exist_ok=True)

    runner = CliRunner()
    first = runner.invoke(main, ["next-action", "--config", str(config_path)])
    assert first.exit_code == 0
    assert json.loads(first.output)["command"] == "runtime"

    step_result = tmp_dir / "step-result.json"
    step_result.write_text(
        json.dumps({"step": "runtime_pre_code", "task_id": "01", "exit_code": 0}),
        encoding="utf-8",
    )
    second = runner.invoke(
        main,
        ["next-action", "--config", str(config_path), "--step-result", str(step_result)],
    )
    assert second.exit_code == 0
    assert json.loads(second.output)["command"] == "code"

    step_result.write_text(
        json.dumps({"step": "code", "task_id": "01", "exit_code": 0}),
        encoding="utf-8",
    )
    third = runner.invoke(
        main,
        ["next-action", "--config", str(config_path), "--step-result", str(step_result)],
    )
    assert third.exit_code == 0
    third_payload = json.loads(third.output)
    assert third_payload["command"] == "runtime"
    assert third_payload["phase"] == "post_code"

    step_result.write_text(
        json.dumps({"step": "runtime_post_code", "task_id": "01", "exit_code": 0}),
        encoding="utf-8",
    )
    fourth = runner.invoke(
        main,
        ["next-action", "--config", str(config_path), "--step-result", str(step_result)],
    )
    assert fourth.exit_code == 0
    fourth_payload = json.loads(fourth.output)
    assert fourth_payload["command"] == "review"
    assert "prompt_file" in fourth_payload
    assert fourth_payload["image"] == "ralph-loop-codex"


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


def test_next_action_returns_orphan_when_task_is_in_progress(
    sample_workspace: Path, monkeypatch
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].status = TaskStatus.IN_PROGRESS
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
    assert payload["command"] == "orphan"
    assert payload["task_ids"] == ["01"]


def test_recover_command_resets_orphan_task_to_failed(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    progress_path = sample_workspace / "PROGRESS.yaml"

    progress = load_progress(str(progress_path))
    progress.phases[0].tasks[0].status = TaskStatus.IN_PROGRESS
    progress.phases[0].tasks[0].retries = 1
    save_progress(progress, str(progress_path))

    runner = CliRunner()
    result = runner.invoke(main, ["recover", "--config", str(config_path), "--task-id", "all"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["recovered"] == ["01"]

    updated = load_progress(str(progress_path))
    assert updated.phases[0].tasks[0].status == TaskStatus.FAILED
    assert updated.phases[0].tasks[0].retries == 1


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


def test_update_command_unified_fails_on_runtime_post_without_missing_review(
    sample_workspace: Path,
) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    config_payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config_payload["review_mode"] = "unified_agent"
    config_path.write_text(yaml.safe_dump(config_payload, sort_keys=False), encoding="utf-8")
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
        "task_base_sha": "abc123",
        "runtime_pre_ok": True,
        "results": [
            {"step": "runtime_pre_code", "task_id": "01", "exit_code": 0},
            {"step": "code", "task_id": "01", "exit_code": 0},
            {
                "step": "runtime_post_code",
                "task_id": "01",
                "exit_code": 1,
                "runtime_command": "./scripts/ralph/guard.sh",
                "stdout": "",
                "stderr": "service unavailable",
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
    assert any(source.type == "runtime_guard" for source in task.feedback[-1].sources)
    assert not any(
        source.type == "review" and source.details == "Review result missing for this attempt."
        for source in task.feedback[-1].sources
    )


def test_build_inspector_prompt_includes_failure_gates() -> None:
    class _Task:
        id = "01"
        title = "Demo"
        visual_verify = {"type": "screenshot", "url": "http://localhost:3001"}

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


def test_run_command_rejects_unified_review_mode(sample_workspace: Path) -> None:
    config_path = sample_workspace / "ralph-config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    payload["review_mode"] = "unified_agent"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["run", "--config", str(config_path), "--sandbox", "none"])

    assert result.exit_code != 0
    assert "does not support `review_mode=unified_agent`" in result.output


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
