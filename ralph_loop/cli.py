from __future__ import annotations

import json
import os
import re
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
from ralph_loop.config import RalphConfig
from ralph_loop.loop import run_loop
from ralph_loop.progress import (
    FeedbackEntry,
    FeedbackSource,
    PhaseStatus,
    Progress,
    TaskStatus,
    complete_task,
    fail_task,
    find_task,
    load_progress,
    lock_task,
    save_progress,
    select_next_task,
)


def _load_config(config_path: str) -> RalphConfig:
    return RalphConfig.load(config_path)


def _load_config_and_progress(config_path: str) -> tuple[RalphConfig, Progress]:
    config = _load_config(config_path)
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
    files_to_touch: list[str] = Field(default_factory=list)
    files_not_to_touch: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    reference_impl: str | None = None


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
    config_path: Path
    config: RalphConfig


@dataclass
class _Paths:
    tmp_dir: Path
    iteration_path: Path
    coder_prompt_path: Path
    inspector_prompt_path: Path


def _runtime_paths(config: RalphConfig) -> _Paths:
    workspace = Path(config.workspace_dir)
    tmp_dir = workspace / ".ralph-tmp"
    return _Paths(
        tmp_dir=tmp_dir,
        iteration_path=tmp_dir / "iteration-state.json",
        coder_prompt_path=tmp_dir / "coder-prompt.md",
        inspector_prompt_path=tmp_dir / "inspector-prompt.md",
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


def _extract_step_result(step_result_path: str | None) -> dict[str, Any] | None:
    if not step_result_path:
        return None
    path = Path(step_result_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return None
    return data


def _build_coder_prompt(task: Any) -> str:
    task_path = Path(str(task.task_file))
    if task_path.exists():
        body = task_path.read_text(encoding="utf-8")
    else:
        body = f"# Task\n\n{task.title}"
    return body


def _build_inspector_prompt(task: Any, verify_results: list[dict[str, Any]] | None) -> str:
    lines = [
        f"# Inspect Task {task.id}: {task.title}",
        "",
        "Review task result and return strict JSON:",
    ]
    lines.append('{"verdict":"pass|fail","feedback":"..."}')
    if verify_results:
        lines.extend(["", "## Verify results", json.dumps(verify_results, indent=2)])
    return "\n".join(lines) + "\n"


def _summary(progress: Progress) -> dict[str, int]:
    tasks = [task for phase in progress.phases for task in phase.tasks]
    completed = sum(1 for task in tasks if task.status == TaskStatus.COMPLETED)
    aborted = sum(1 for task in tasks if task.status == TaskStatus.ABORT)
    return {"completed": completed, "aborted": aborted, "total": len(tasks)}


def _parse_inspector_output(stdout: str) -> tuple[str, str]:
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, dict):
            verdict = str(parsed.get("verdict", "fail")).strip().lower()
            feedback = str(parsed.get("feedback", "")).strip()
            if verdict in {"pass", "fail"}:
                return verdict, feedback
    except json.JSONDecodeError:
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


def _select_available_backend() -> tuple[str, Any]:
    for engine in ("codex", "copilot", "claude"):
        backend = get_backend(engine)
        if backend.is_available():
            return engine, backend
    raise click.ClickException("No supported AI CLI found in PATH (codex/copilot/claude)")


def _run_prompt_with_available_backend(prompt_file: Path) -> int:
    if not prompt_file.exists():
        raise click.ClickException(f"Prompt file not found: {prompt_file}")

    _, backend = _select_available_backend()
    result = backend.execute(prompt=str(prompt_file), cwd=str(prompt_file.parent))

    if result.stdout:
        click.echo(result.stdout, nl=False)
    if result.stderr:
        click.echo(result.stderr, err=True, nl=False)

    return int(result.exit_code)


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
            for mount_path in mount_values:
                if not isinstance(mount_path, str):
                    continue
                expanded = Path(mount_path).expanduser()
                if not expanded.exists():
                    continue
                command.extend(["-v", f"{expanded}:{expanded}:ro"])

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


def _ensure_config(config_path: Path) -> RalphConfig:
    if config_path.exists():
        return RalphConfig.load(str(config_path))

    scaffold = {
        "progress_file": "PROGRESS.yaml",
        "task_dir": "tasks/",
        "max_retries": 3,
        "pause_file": "PAUSE.md",
        "workspace_dir": ".",
        "backends": {
            "coder": {"engine": "codex", "model": "gpt-5.3-codex", "timeout_seconds": 600},
            "inspector": {"engine": "copilot", "model": "claude-opus-4-6", "timeout_seconds": 300},
        },
        "verify_commands": [],
        "auth": {},
        "project_instructions": None,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(scaffold, sort_keys=False), encoding="utf-8")
    return RalphConfig.load(str(config_path))


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
    tasks: list[GeneratedTask] = []
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("- [ ]", "- ", "* ")):
            candidate = stripped.split(" ", 2)[-1].strip()
            if candidate:
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

    return GeneratedPlan(
        title=title_hint, phases=[GeneratedPhase(id=1, name="Phase 1", tasks=tasks)]
    )


def _build_prompt(source_content: str, config: RalphConfig) -> str:
    project_instructions = ""
    if config.project_instructions:
        instructions_path = Path(config.project_instructions)
        if instructions_path.exists():
            project_instructions = instructions_path.read_text(encoding="utf-8")

    template = _load_plan_template()
    return template.render(source_content=source_content, project_instructions=project_instructions)


def _generate_plan_with_backend(
    *,
    config: RalphConfig,
    prompt: str,
    backend_override: str | None,
    model_override: str | None,
) -> GeneratedPlan | None:
    role_backend = (
        config.get_backend("inspector")
        if "inspector" in config.backends
        else config.get_backend("coder")
    )
    engine = backend_override or role_backend.engine
    model = model_override or role_backend.model

    backend = get_backend(engine)
    if not backend.is_available():
        return None

    result = backend.execute(
        prompt=prompt,
        model=model,
        timeout_seconds=role_backend.timeout_seconds,
        extra_flags=role_backend.extra_flags,
        cwd=config.workspace_dir,
    )

    if result.exit_code != 0:
        return None

    try:
        payload = _extract_first_json_object(result.stdout)
        return GeneratedPlan.model_validate(payload)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return None


def _format_task_markdown(phase_id: int, task: GeneratedTask) -> str:
    acceptance = (
        "\n".join(
            [
                f"{index + 1}. {criterion}"
                for index, criterion in enumerate(task.acceptance_criteria)
            ]
        )
        or "1. Complete the requested implementation."
    )
    files_to_touch = (
        "\n".join([f"- {file_path}" for file_path in task.files_to_touch]) or "- (to define)"
    )
    test_plan = task.test_plan or "1. Run the configured verification commands."
    constraints = "\n".join([f"- {constraint}" for constraint in task.constraints]) or "- None"
    files_not_to_touch = "\n".join([f"- {file_path}" for file_path in task.files_not_to_touch])
    verify_commands = "\n".join([f'  - "{command}"' for command in task.verify_commands])
    if not verify_commands:
        verify_commands = '  - ""'

    reference_section = ""
    if task.reference_impl:
        reference_section = f"\n\n## Reference Implementation\n\n{task.reference_impl}"

    not_to_touch_section = ""
    if files_not_to_touch:
        not_to_touch_section = f"\n{files_not_to_touch}"

    return (
        f"---\n"
        f"phase: {phase_id}\n"
        f"priority: {task.priority}\n"
        f"verify_commands:\n"
        f"{verify_commands}\n"
        f"visual_verify: null\n"
        f"contract_file: null\n"
        f"files_to_touch:\n"
        + "\n".join([f'  - "{path}"' for path in task.files_to_touch])
        + ("\n" if task.files_to_touch else '\n  - ""\n')
        + "files_not_to_touch:\n"
        + "\n".join([f'  - "{path}"' for path in task.files_not_to_touch])
        + ("\n" if task.files_not_to_touch else '\n  - ""\n')
        + "---\n\n"
        + f"# Task {task.id}: {task.title}\n\n"
        + f"**Phase**: {phase_id}\n\n"
        + "## Description\n\n"
        + f"{task.description}\n\n"
        + "## Acceptance Criteria\n\n"
        + f"{acceptance}\n\n"
        + "## Files to Create/Modify\n\n"
        + f"{files_to_touch}\n\n"
        + "## Test Plan\n\n"
        + f"{test_plan}\n\n"
        + "## Constraints\n\n"
        + f"{constraints}{not_to_touch_section}"
        + reference_section
        + "\n"
    )


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
            slug = _slugify(task.title)
            task_filename = f"{task_id}-{slug}.md"
            task_path = task_dir / task_filename
            effective_task = task.model_copy(update={"id": task_id})
            task_path.write_text(
                _format_task_markdown(phase_index, effective_task),
                encoding="utf-8",
            )

            phase_tasks.append(
                {
                    "id": task_id,
                    "title": task.title,
                    "task_file": str(task_path.resolve()),
                    "contract_file": None,
                    "status": "not_started",
                    "retries": 0,
                    "verify_commands": task.verify_commands,
                    "visual_verify": None,
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


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """ralph-loop command line interface."""


@main.command("status")
@click.option("--config", "config_path", default="ralph-config.yaml", show_default=True)
def status_command(config_path: str) -> None:
    """Print a summary of PROGRESS.yaml."""
    config, progress = _load_config_and_progress(config_path)

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
@click.option("--config", "config_path", default="ralph-config.yaml", show_default=True)
def list_engines_command(config_path: str) -> None:
    """Print unique engine names from configured backend roles."""
    config = _load_config(config_path)
    engines = sorted({backend.engine for backend in config.backends.values()})
    for engine in engines:
        click.echo(engine)


@main.command("validate")
@click.option("--config", "config_path", default="ralph-config.yaml", show_default=True)
def validate_command(config_path: str) -> None:
    """Validate consistency between config, progress, and task files."""
    config, progress = _load_config_and_progress(config_path)

    task_dir = Path(config.task_dir)
    progress_task_files = {
        Path(task.task_file).resolve() for phase in progress.phases for task in phase.tasks
    }

    if not task_dir.exists():
        raise click.ClickException(f"Task directory does not exist: {task_dir}")

    task_files_on_disk = set(task_dir.glob("*.md"))
    missing_on_disk = [path for path in sorted(progress_task_files) if not path.exists()]
    if missing_on_disk:
        raise click.ClickException(
            f"Task files referenced in progress are missing: {missing_on_disk}"
        )

    not_referenced = [
        path for path in sorted(task_files_on_disk) if path.resolve() not in progress_task_files
    ]
    if not_referenced:
        raise click.ClickException(f"Task files not referenced in progress: {not_referenced}")

    known_engines = {"codex", "copilot", "claude"}
    unknown = sorted({backend.engine for backend in config.backends.values()} - known_engines)
    if unknown:
        raise click.ClickException(f"Unknown backend engines: {unknown}")

    for engine in {backend.engine for backend in config.backends.values()}:
        _ = config.get_auth(engine)

    click.echo("Validation passed.")


@main.command("next-action")
@click.option("--config", "config_path", required=True)
@click.option("--step-result", "step_result_path", required=False)
def next_action_command(config_path: str, step_result_path: str | None) -> None:
    """Determine next orchestration action."""
    config = _load_config(config_path)
    progress = load_progress(config.progress_file)
    paths = _runtime_paths(config)
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)

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
    if iteration is not None and step_result is not None:
        _append_step_result(iteration, step_result)

        task_id = str(iteration.get("task_id", ""))
        task = find_task(progress, task_id)
        step_name = str(step_result.get("step", ""))

        if step_name == "code":
            verify_commands = task.verify_commands or config.verify_commands
            if verify_commands:
                iteration["current_step"] = "verify"
                _save_iteration_state(paths.iteration_path, iteration)
                click.echo(
                    json.dumps(
                        {
                            "command": "verify",
                            "task_id": task.id,
                            "commands": verify_commands,
                        }
                    )
                )
                return

            inspector_prompt = _build_inspector_prompt(task, None)
            paths.inspector_prompt_path.write_text(inspector_prompt, encoding="utf-8")
            inspector = config.get_backend("inspector")
            iteration["current_step"] = "inspect"
            _save_iteration_state(paths.iteration_path, iteration)
            click.echo(
                json.dumps(
                    {
                        "command": "inspect",
                        "task_id": task.id,
                        "image": f"ralph-loop-{inspector.engine}",
                        "prompt_file": str(
                            paths.inspector_prompt_path.relative_to(Path(config.workspace_dir))
                        ),
                        "model": inspector.model,
                        "timeout_seconds": inspector.timeout_seconds,
                        "auth": config.get_auth(inspector.engine).model_dump(),
                    }
                )
            )
            return

        if step_name == "verify":
            results = step_result.get("results")
            verify_results = results if isinstance(results, list) else None
            inspector_prompt = _build_inspector_prompt(task, verify_results)
            paths.inspector_prompt_path.write_text(inspector_prompt, encoding="utf-8")
            inspector = config.get_backend("inspector")
            iteration["current_step"] = "inspect"
            _save_iteration_state(paths.iteration_path, iteration)
            click.echo(
                json.dumps(
                    {
                        "command": "inspect",
                        "task_id": task.id,
                        "image": f"ralph-loop-{inspector.engine}",
                        "prompt_file": str(
                            paths.inspector_prompt_path.relative_to(Path(config.workspace_dir))
                        ),
                        "model": inspector.model,
                        "timeout_seconds": inspector.timeout_seconds,
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

    if iteration is not None and iteration.get("current_step") == "update":
        _clear_iteration_state(paths.iteration_path)

    next_task = select_next_task(progress, config.max_retries)
    if next_task is None:
        summary = _summary(progress)
        reason = "all_completed" if summary["aborted"] == 0 else "remaining_tasks_aborted"
        click.echo(json.dumps({"command": "done", "reason": reason, "summary": summary}))
        return

    lock_task(progress, next_task.id)
    save_progress(progress, config.progress_file)
    coder_prompt = _build_coder_prompt(next_task)
    paths.coder_prompt_path.write_text(coder_prompt, encoding="utf-8")

    iteration = {
        "task_id": next_task.id,
        "current_step": "code",
        "started_at": _now_iso(),
        "results": [],
    }
    _save_iteration_state(paths.iteration_path, iteration)

    coder = config.get_backend("coder")
    click.echo(
        json.dumps(
            {
                "command": "code",
                "task_id": next_task.id,
                "image": f"ralph-loop-{coder.engine}",
                "prompt_file": str(paths.coder_prompt_path.relative_to(Path(config.workspace_dir))),
                "model": coder.model,
                "timeout_seconds": coder.timeout_seconds,
                "auth": config.get_auth(coder.engine).model_dump(),
            }
        )
    )


@main.command("run")
@click.option("--config", "config_path", default="ralph-config.yaml", show_default=True)
@click.option("--sandbox", type=click.Choice(["none", "docker"]), default="none", show_default=True)
def run_command(config_path: str, sandbox: str) -> None:
    """Run the native orchestration loop."""
    config = _load_config(config_path)
    raise SystemExit(run_loop(config, sandbox=sandbox))


@main.command("execute")
@click.option("--prompt-file", required=True)
def execute_command(prompt_file: str) -> None:
    """Execute a coding prompt using an available AI CLI backend."""
    exit_code = _run_prompt_with_available_backend(Path(prompt_file))
    if exit_code != 0:
        raise SystemExit(exit_code)


@main.command("inspect")
@click.option("--prompt-file", required=True)
def inspect_command(prompt_file: str) -> None:
    """Execute an inspection prompt using an available AI CLI backend."""
    exit_code = _run_prompt_with_available_backend(Path(prompt_file))
    if exit_code != 0:
        raise SystemExit(exit_code)


@main.command("update")
@click.option("--config", "config_path", required=True)
@click.option("--result-dir", required=True)
def update_command(config_path: str, result_dir: str) -> None:
    """Aggregate step results and update progress."""
    config = _load_config(config_path)
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

    all_passed = True
    feedback_sources: list[FeedbackSource] = []

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

        if step_name == "inspect":
            verdict, feedback = _parse_inspector_output(str(item.get("stdout", "")))
            if verdict != "pass":
                all_passed = False
            feedback_sources.append(
                FeedbackSource(type="ai_inspection", verdict=verdict, details=feedback)
            )

    if all_passed:
        complete_task(progress, task.id)
    else:
        entry = FeedbackEntry(
            attempt=task.retries + 1, timestamp=_now_iso(), sources=feedback_sources
        )
        fail_task(progress, task.id, entry, config.max_retries)

    save_progress(progress, config.progress_file)
    _clear_iteration_state(paths.iteration_path)


@main.command("reset")
@click.argument("task_id")
@click.option("--config", "config_path", default="ralph-config.yaml", show_default=True)
def reset_command(task_id: str, config_path: str) -> None:
    """Reset a task to not_started state."""
    config, progress = _load_config_and_progress(config_path)

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


@main.command("init")
@click.option("--from", "source_path", required=True)
@click.option("--backend", required=False)
@click.option("--model", required=False)
@click.option("--config", "config_path", default="ralph-config.yaml", show_default=True)
def init_command(
    source_path: str, backend: str | None, model: str | None, config_path: str
) -> None:
    """Generate tasks and progress from a plan document."""
    source = Path(source_path)
    if not source.exists():
        raise click.ClickException(f"Source plan not found: {source}")

    config_file = Path(config_path)
    config = _ensure_config(config_file)
    context = _InitContext(source_path=source, config_path=config_file, config=config)

    source_content = source.read_text(encoding="utf-8")
    prompt = _build_prompt(source_content, config)

    generated_plan = _generate_plan_with_backend(
        config=config,
        prompt=prompt,
        backend_override=backend,
        model_override=model,
    )
    if generated_plan is None:
        generated_plan = _fallback_plan(source_content, source.stem.replace("-", " ").title())

    count, task_dir, progress_path = _write_generated_artifacts(context, generated_plan)
    click.echo(f"Generated {count} task file(s) in {task_dir}")
    click.echo(f"Generated progress file: {progress_path}")
