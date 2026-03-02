# ralph-loop — Standalone Public Package Plan

## Overview

`ralph-loop` is a pip-installable / Docker-primary Python package that orchestrates AFK AI coding loops with multi-model-per-phase strategy, feedback accumulation across retries, and hybrid verification (deterministic + AI inspection + visual). Ships with 3 backends (Codex, Copilot, Claude Code) from v1.

## Core Differentiators

| Differentiator | Ralphy (2.5K★) | ralph-gold | ralph-loop |
|---|---|---|---|
| Multi-model per task phase | ❌ | ❌ | ✅ |
| Feedback accumulation across retries | ❌ | ❌ | ✅ |
| AI-powered diff inspection as gate | ❌ | Partial | ✅ |
| Visual screenshot verification | ❌ | ❌ | ✅ |

## Repository & Distribution

- **Name**: `ralph-loop`
- **Hosting**: Personal GitHub account (public, MIT license)
- **Primary distribution**: Docker (bash script builds/runs containers)
- **Secondary distribution**: `uvx ralph-loop run` (Python package via PyPI)
- **Integration**: Git submodule into consumer projects
- **Python**: ≥ 3.11
- **License**: MIT

---

## Full Directory Structure

```
ralph-loop/
├── ralph-loop                          # [BASH] Host entry point — Docker-primary CLI
├── Dockerfile                          # Multi-stage: base → codex, copilot, claude
├── .dockerignore
├── pyproject.toml                      # Python package config (for uvx path)
├── README.md
├── LICENSE                             # MIT
├── .gitignore
├── .github/
│   └── workflows/
│       ├── ci.yml                      # Tests + lint + type check + Docker build
│       └── publish.yml                 # PyPI publish on tag
│
├── ralph_loop/                         # ── Python package ──
│   ├── __init__.py                     # __version__ = "0.1.0"
│   ├── __main__.py                     # `python -m ralph_loop` entry
│   │
│   ├── cli.py                          # Click CLI: subcommands for container + native modes
│   │   # Commands:
│   │   #   next-action  --config PATH --step-result PATH  → writes .ralph-tmp/next-action.json
│   │   #   update       --config PATH --result-dir PATH   → updates PROGRESS.yaml
│   │   #   execute      --prompt-file PATH                → calls AI CLI, sets exit code
│   │   #   inspect      --prompt-file PATH                → calls AI CLI, writes verdict
│   │   #   run          --config PATH [--sandbox docker]  → native loop (uvx path)
│   │   #   status       --config PATH                     → prints progress summary
│   │   #   reset        TASK_ID --config PATH             → resets task to not_started
│   │   #   validate     --config PATH                     → validates config + progress
│   │   #   init         --from PATH [--backend] [--model] → plan-to-tasks conversion
│   │   #   build-images                                   → builds all Docker images
│   │
│   ├── loop.py                         # Native loop orchestrator (for `uvx ralph-loop run`)
│   ├── config.py                       # RalphConfig Pydantic model (ralph-config.yaml)
│   ├── progress.py                     # Progress state machine (PROGRESS.yaml CRUD)
│   ├── task.py                         # Task model + .md frontmatter parser
│   ├── feedback.py                     # FeedbackEntry model + prompt formatter
│   │
│   ├── backends/                       # ── AI CLI backend abstraction ──
│   │   ├── __init__.py                 # Backend registry: get_backend(name) → Backend
│   │   ├── base.py                     # Backend Protocol + ExecutionResult dataclass
│   │   ├── codex.py                    # CodexBackend: codex exec wrapper
│   │   ├── copilot.py                  # CopilotBackend: copilot -p wrapper
│   │   ├── claude.py                   # ClaudeBackend: claude -p wrapper
│   │   └── sandbox.py                  # SandboxBackend: wraps any Backend in Docker container
│   │
│   ├── verification/                   # ── Verification pipeline ──
│   │   ├── __init__.py
│   │   ├── deterministic.py            # Run verify_commands on host, collect ALL results
│   │   ├── ai_inspection.py            # Build inspection prompt, call inspector backend
│   │   ├── visual.py                   # Screenshot capture + AI comparison (optional)
│   │   └── pipeline.py                 # Orchestrate all verifiers, aggregate VerificationReport
│   │
│   ├── prompts/                        # ── Jinja2 prompt templates ──
│   │   ├── coder.md.j2                 # Task + feedback + context → coder prompt
│   │   ├── inspector.md.j2             # Diff + criteria + contract → inspector prompt
│   │   └── plan_to_tasks.md.j2         # Source plan → tasks + PROGRESS.yaml
│   │
│   └── skills/                         # ── Agent skills (agentskills.io) ──
│       ├── run-preflight/
│       │   └── SKILL.md                # Skill: run verification commands
│       └── visual-compare/
│           └── SKILL.md                # Skill: screenshot comparison workflow
│
├── docker/
│   └── entrypoint.sh                   # Container startup: git safe.directory, auth check
│
├── docs/
│   ├── DESIGN.md                       # This document (living spec)
│   ├── plan-to-tasks.md                # Standalone prompt for manual plan conversion
│   └── configuration.md                # Full config reference
│
├── prompts/
│   └── plan-to-tasks.prompt.md         # VS Code Copilot slash command version
│
├── examples/
│   ├── minimal-2-tasks/                # 2-task example: ralph-config.yaml + tasks/ + PROGRESS.yaml
│   │   ├── ralph-config.yaml
│   │   ├── PROGRESS.yaml
│   │   └── tasks/
│   │       ├── 01-hello-world.md
│   │       └── 02-add-tests.md
│   └── multi-phase-visual/             # 6-task, 2-phase example with visual verification
│       ├── ralph-config.yaml
│       ├── PROGRESS.yaml
│       └── tasks/
│
└── tests/
    ├── conftest.py                     # Shared fixtures: tmp progress files, mock backends
    ├── test_progress.py                # YAML round-trip, state transitions, task selection
    ├── test_config.py                  # Config validation, defaults, merging
    ├── test_feedback.py                # Feedback accumulation, prompt formatting
    ├── test_task.py                    # Frontmatter parsing, task model
    ├── test_backends.py                # Mock subprocess for each backend
    ├── test_verification.py            # Verification pipeline with mocked backends
    ├── test_cli.py                     # Click CLI smoke tests (CliRunner)
    └── test_integration.py             # Full loop with mocked backends on 3-task PROGRESS.yaml
```

## Architecture: Host Orchestrator + Specialized Containers

```
Host (bash script: ralph-loop)
│
│  ┌─────────────────────────────────┐
├──│ ralph-loop-base                  │  "What's the next task?"
│  │ Python + ralph_loop package      │  → Returns JSON: next action
│  └─────────────────────────────────┘
│
│  ┌─────────────────────────────────┐
├──│ ralph-loop-codex                 │  "Execute this coding prompt"
│  │ base + codex CLI                 │  → Writes code to mounted workspace
│  └─────────────────────────────────┘
│
│  bash -lc "verify_command"            (runs on HOST, user-configured trusted command)
│
│  ┌─────────────────────────────────┐
├──│ ralph-loop-copilot               │  "Inspect this diff"
│  │ base + copilot CLI               │  → Returns verdict + feedback
│  └─────────────────────────────────┘
│
│  ┌─────────────────────────────────┐
└──│ ralph-loop-base                  │  "Update progress with results"
     │ Python                           │  → Writes PROGRESS.yaml
     └─────────────────────────────────┘
```

**Key design properties:**
- No Docker socket mount — true isolation per container
- Each container is independent and disposable
- Bash script is a dumb dispatcher — the brain is Python inside `ralph-loop-base`
- Deterministic tests run on HOST via user-configured commands
- `verify_commands` are trusted user inputs, executed in an isolated subshell (`bash -lc`) without `eval`
- Communication via `.ralph-tmp/` files in workspace (not pipes)

---

## `pyproject.toml` Specifications

```toml
[project]
name = "ralph-loop"
version = "0.1.0"
description = "AFK AI coding loop orchestrator with multi-model strategy and feedback accumulation"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "pyyaml>=6.0",
    "jinja2>=3.1",
    "pydantic>=2.0",
    "python-frontmatter>=1.1",
]

[project.optional-dependencies]
visual = ["playwright>=1.40", "pillow>=10.0"]
dev = ["pytest>=8.0", "pytest-cov", "ruff", "mypy"]

[project.scripts]
ralph-loop = "ralph_loop.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.mypy]
python_version = "3.11"
strict = true
```

## Execution Modes

| Mode | Command | Requirements | AI CLIs Run In | Isolation |
|---|---|---|---|---|
| **Docker primary** | `./ralph-loop run` | Docker only | Per-CLI containers | Full |
| **uvx native** | `uvx ralph-loop run` | uv + Python + CLIs on host | Host (subprocess) | None |
| **uvx + sandbox** | `uvx ralph-loop run --sandbox docker` | uv + Python + Docker | Per-CLI containers | Per-call |

---

## Pydantic Data Models (Detailed)

### `config.py` — `RalphConfig`

```python
from __future__ import annotations
from pydantic import BaseModel, Field

class BackendConfig(BaseModel):
    """Configuration for a single AI backend role."""
    engine: str                          # "codex" | "copilot" | "claude"
    model: str | None = None             # Model override (e.g., "gpt-5.3-codex")
    extra_flags: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600           # Shell-level timeout for CLI call
    max_turns: int | None = None         # Claude-specific: --max-turns
    budget_usd: float | None = None      # Claude-specific: --max-budget-usd

class AuthConfig(BaseModel):
    """Auth credentials forwarding for Docker containers."""
    env: list[str] = Field(default_factory=list)     # Env vars to forward: ["OPENAI_API_KEY"]
    mount: list[str] = Field(default_factory=list)    # Host dirs to mount: ["~/.config/gh"]

class VisualVerifyConfig(BaseModel):
    """Configuration for visual screenshot verification of a task."""
    type: str = "screenshot"               # "screenshot" | "diff_image"
    url: str                               # URL to capture
    reference: str | None = None           # Optional path to reference image (relative to workspace)
    assertion: str                         # Natural language: what to check
    viewport_width: int = 1280
    viewport_height: int = 720

class RalphConfig(BaseModel):
    """Root configuration loaded from a global config file."""
    # Runtime paths are resolved per loop directory by load(..., loop_dir=...):
    #   progress_file -> <loop_dir>/PROGRESS.yaml
    #   task_dir -> <loop_dir>/tasks/
    #   pause_file -> <loop_dir>/PAUSE.md
    #   workspace_dir -> <loop_dir>/product/
    progress_file: str
    task_dir: str
    max_retries: int = 3
    pause_file: str
    workspace_dir: str

    backends: dict[str, BackendConfig]      # Keys: "coder", "inspector", "visual"
    verify_commands: list[str] = Field(default_factory=list)  # Default verify commands
    auth: dict[str, AuthConfig] = Field(default_factory=dict) # Keys: "codex", "copilot", "claude"

    # Optional: project instructions file path (AGENTS.md, CLAUDE.md)
    project_instructions: str | None = None

    @classmethod
    def load(cls, path: str, *, loop_dir: str | None = None) -> RalphConfig:
        """Load global defaults and resolve runtime paths for a loop directory."""
        ...

    def get_backend(self, role: str) -> BackendConfig:
        """Get backend config for a role, raise if not configured."""
        ...

    def get_auth(self, engine: str) -> AuthConfig:
        """Get auth config for an engine, return empty if not configured."""
        ...
```

### `progress.py` — Progress State Machine

```python
from __future__ import annotations
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field

class TaskStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORT = "abort"

class PhaseStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class FeedbackSource(BaseModel):
    """One verification result from one source."""
    type: str                        # "test" | "ai_inspection" | "visual"
    verdict: str                     # "pass" | "fail"
    command: str | None = None       # For test type: the command that was run
    exit_code: int | None = None     # For test type: process exit code
    output: str | None = None        # Truncated stdout+stderr (max 4000 chars)
    details: str | None = None       # For AI type: the AI's feedback text

class FeedbackEntry(BaseModel):
    """Accumulated feedback from one failed attempt."""
    attempt: int
    timestamp: str                   # ISO 8601
    sources: list[FeedbackSource] = Field(default_factory=list)

class TaskProgress(BaseModel):
    """Runtime state of a single task (lives in PROGRESS.yaml)."""
    id: str
    title: str
    task_file: str                   # Relative path: "tasks/01-slug.md"
    contract_file: str | None = None
    status: TaskStatus = TaskStatus.NOT_STARTED
    retries: int = 0
    verify_commands: list[str] = Field(default_factory=list)  # Override from task frontmatter
    visual_verify: VisualVerifyConfig | None = None
    feedback: list[FeedbackEntry] = Field(default_factory=list)

class PhaseProgress(BaseModel):
    """Runtime state of a phase."""
    id: int
    name: str
    status: PhaseStatus = PhaseStatus.NOT_STARTED
    tasks: list[TaskProgress] = Field(default_factory=list)

class ProgressMeta(BaseModel):
    title: str
    started: str                     # ISO date: "2026-03-01"
    current_phase: int = 1

class Progress(BaseModel):
    """Root model for PROGRESS.yaml."""
    meta: ProgressMeta
    phases: list[PhaseProgress] = Field(default_factory=list)


# ── State machine functions ──

def load_progress(path: str) -> Progress:
    """Parse PROGRESS.yaml into Progress model. Raises FileNotFoundError."""
    ...

def save_progress(progress: Progress, path: str) -> None:
    """Atomic write: write to .tmp file, then os.rename() to target path."""
    ...

def select_next_task(progress: Progress, max_retries: int = 3) -> TaskProgress | None:
    """Select next task to work on.
    
    Priority order:
    1. Tasks with status=FAILED and retries < max_retries (retry first)
    2. Tasks with status=NOT_STARTED
    
    Within each priority, select from the current phase first.
    Returns None if all tasks are completed or aborted.
    """
    ...

def lock_task(progress: Progress, task_id: str) -> None:
    """Set task status to IN_PROGRESS. 
    
    Legal transitions: NOT_STARTED → IN_PROGRESS, FAILED → IN_PROGRESS
    Raises ValueError on illegal transition.
    """
    ...

def complete_task(progress: Progress, task_id: str) -> None:
    """Set task status to COMPLETED.
    
    Legal transition: IN_PROGRESS → COMPLETED
    Also checks if all tasks in the phase are completed → updates phase status.
    """
    ...

def fail_task(
    progress: Progress, 
    task_id: str, 
    feedback: FeedbackEntry, 
    max_retries: int = 3
) -> None:
    """Increment retries, append feedback, set status.
    
    Legal transition: IN_PROGRESS → FAILED (if retries < max) or IN_PROGRESS → ABORT (if retries >= max)
    """
    ...

def find_task(progress: Progress, task_id: str) -> TaskProgress:
    """Find task by ID across all phases. Raises KeyError if not found."""
    ...

def get_current_phase(progress: Progress) -> PhaseProgress:
    """Get the current phase based on meta.current_phase."""
    ...

def advance_phase(progress: Progress) -> bool:
    """If current phase is all completed, advance current_phase to next.
    Returns True if advanced, False if there is no next phase (all done).
    """
    ...
```

### State Transition Diagram

```
                    ┌──────────────┐
                    │  NOT_STARTED │
                    └──────┬───────┘
                           │ lock_task()
                           ▼
                    ┌──────────────┐
              ┌────►│  IN_PROGRESS │◄────┐
              │     └──────┬───────┘     │
              │            │             │
              │     ┌──────┴───────┐     │
              │     │              │     │
              │     ▼              ▼     │
        ┌───────────┐      ┌──────────┐ │
        │ COMPLETED │      │  FAILED  │─┘  (retries < max → lock_task() again)
        └───────────┘      └────┬─────┘
                                │ (retries >= max)
                                ▼
                         ┌──────────┐
                         │  ABORT   │
                         └──────────┘

Phase transitions:
  NOT_STARTED → IN_PROGRESS  (when first task in phase is locked)
  IN_PROGRESS → COMPLETED    (when all tasks in phase are completed/abort)
```

### `task.py` — Task File Model

```python
from __future__ import annotations
from pydantic import BaseModel, Field
import frontmatter  # python-frontmatter library

class TaskFrontmatter(BaseModel):
    """YAML frontmatter parsed from a task .md file."""
    phase: int
    priority: str = "medium"                          # "high" | "medium" | "low"
    verify_commands: list[str] = Field(default_factory=list)
    visual_verify: VisualVerifyConfig | None = None
    contract_file: str | None = None
    files_to_touch: list[str] = Field(default_factory=list)
    files_not_to_touch: list[str] = Field(default_factory=list)

class Task(BaseModel):
    """Full task model: frontmatter + markdown body + runtime state."""
    frontmatter: TaskFrontmatter
    body: str                        # Full markdown content after frontmatter
    file_path: str                   # Relative path to .md file

    # Extracted sections (parsed from markdown body):
    title: str
    description: str
    acceptance_criteria: list[str]   # Each criterion as a string
    test_plan: str                   # Raw markdown of test plan section
    reference_impl: str | None = None
    constraints: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> Task:
        """Parse a task .md file into a Task model.
        
        Uses python-frontmatter to split YAML header from markdown body.
        Parses markdown body to extract sections by ## headers.
        """
        ...

    def get_verify_commands(self, defaults: list[str]) -> list[str]:
        """Return task-specific verify_commands if defined, otherwise defaults."""
        return self.frontmatter.verify_commands or defaults
```

### `feedback.py` — Feedback Formatting

```python
from __future__ import annotations

def format_feedback_for_prompt(feedback_entries: list[FeedbackEntry]) -> str:
    """Render accumulated feedback as markdown for injection into coder prompt.
    
    Output format:
    
    ## Previous Attempt Feedback
    
    ### Attempt 1 (2026-03-01T14:32:00Z)
    
    **Test failures:**
    - Command: `./bin/run-tests tests/...`
    - Exit code: 1
    - Output:
      ```
      FAILED test_happy_path - AssertionError: expected 200 got 422
      ```
    
    **AI inspection:**
    - Verdict: FAIL
    - Details: Missing error handling for empty input. Criterion #3 not met.
    
    ### Attempt 2 (2026-03-01T14:45:00Z)
    ...
    
    Truncates individual output fields to 4000 chars to avoid prompt bloat.
    """
    ...

def truncate_output(output: str, max_chars: int = 4000) -> str:
    """Truncate output preserving the last N chars (tail behavior)."""
    ...
```

---

## Docker Images (1 Dockerfile, 4 targets)

```dockerfile
# ── Stage: base ──
FROM python:3.12-slim AS base

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    git jq curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Node.js 22 LTS (needed for codex + claude CLIs)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install ralph-loop Python package
COPY pyproject.toml README.md LICENSE /app/
COPY ralph_loop/ /app/ralph_loop/
RUN pip install --no-cache-dir /app

# Non-root user (matches typical host UID 1000)
RUN useradd -m -u 1000 ralph
USER ralph

WORKDIR /workspace
ENTRYPOINT ["ralph-loop"]

# ── Stage: dev ──
FROM base AS dev
USER root
RUN pip install --no-cache-dir pytest pytest-cov ruff mypy
USER ralph
ENTRYPOINT []
CMD ["bash"]

# ── Stage: codex ──
FROM base AS codex
USER root
RUN npm install -g @openai/codex
USER ralph

# ── Stage: copilot ──
FROM base AS copilot
USER root
RUN curl -fsSL https://gh.io/copilot-install | bash
USER ralph

# ── Stage: claude ──
FROM base AS claude
USER root
RUN npm install -g @anthropic-ai/claude-code
USER ralph
```

Build commands:
```bash
docker build --target base    -t ralph-loop-base    .
docker build --target dev     -t ralph-loop-dev     .
docker build --target codex   -t ralph-loop-codex   .
docker build --target copilot -t ralph-loop-copilot .
docker build --target claude  -t ralph-loop-claude  .
```

**Container entrypoint:** `ralph-loop` (the Python CLI). Each container receives a CLI subcommand (not the bash script).

**`docker/entrypoint.sh`** (alternative entrypoint for custom setup):
```bash
#!/bin/bash
set -e
# Mark mounted workspace as safe for git
git config --global --add safe.directory /workspace
# Validate at least one AI CLI auth is present
if [ -n "$OPENAI_API_KEY" ] || [ -d "$HOME/.config/gh" ] || [ -n "$ANTHROPIC_API_KEY" ]; then
    exec ralph-loop "$@"
else
    echo "⚠️  Warning: No AI CLI auth detected. Proceeding anyway..."
    exec ralph-loop "$@"
fi
```

---

## Backend Abstraction (Detailed)

### `backends/base.py` — Protocol + Result

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

@dataclass
class ExecutionResult:
    """Result of an AI CLI execution."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False

@runtime_checkable
class Backend(Protocol):
    """Protocol that all AI CLI backends must implement."""
    
    @property
    def name(self) -> str:
        """Backend identifier: 'codex', 'copilot', 'claude'."""
        ...
    
    def execute(
        self,
        prompt: str,
        model: str | None = None,
        timeout_seconds: int = 600,
        extra_flags: list[str] | None = None,
        cwd: str | None = None,
    ) -> ExecutionResult:
        """Execute a prompt via the AI CLI.
        
        Args:
            prompt: The prompt text or path to prompt file.
            model: Model override. None = CLI default.
            timeout_seconds: Shell-level timeout.
            extra_flags: Additional CLI flags.
            cwd: Working directory for the subprocess.
        
        Returns:
            ExecutionResult with captured output.
        """
        ...
    
    def is_available(self) -> bool:
        """Check if the CLI binary is installed and accessible."""
        ...
```

### `backends/codex.py`

```python
class CodexBackend:
    name = "codex"
    
    def execute(self, prompt, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
        cmd = ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox"]
        if model:
            cmd.extend(["-m", model])
        if extra_flags:
            cmd.extend(extra_flags)
        cmd.append(prompt)
        # Wrap with shell `timeout` command
        cmd = ["timeout", str(timeout_seconds)] + cmd
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        ...
    
    def is_available(self):
        return shutil.which("codex") is not None
```

### `backends/copilot.py`

```python
class CopilotBackend:
    name = "copilot"
    
    def execute(self, prompt, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
        cmd = ["copilot", "-p", "--allow-all-tools"]
        if model:
            cmd.extend(["--model", model])
        if extra_flags:
            cmd.extend(extra_flags)
        cmd.append(prompt)
        cmd = ["timeout", str(timeout_seconds)] + cmd
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        ...
    
    def is_available(self):
        return shutil.which("copilot") is not None
```

### `backends/claude.py`

```python
class ClaudeBackend:
    name = "claude"
    
    def __init__(self, max_turns: int = 20, budget_usd: float | None = None):
        self.max_turns = max_turns
        self.budget_usd = budget_usd
    
    def execute(self, prompt, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
        cmd = ["claude", "-p", "--dangerously-skip-permissions"]
        cmd.extend(["--max-turns", str(self.max_turns)])
        cmd.extend(["--output-format", "json"])
        if model:
            cmd.extend(["--model", model])
        if self.budget_usd:
            cmd.extend(["--max-budget-usd", str(self.budget_usd)])
        if extra_flags:
            cmd.extend(extra_flags)
        cmd.append(prompt)
        cmd = ["timeout", str(timeout_seconds)] + cmd
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        ...
    
    def is_available(self):
        return shutil.which("claude") is not None
```

### `backends/__init__.py` — Registry

```python
BACKEND_REGISTRY: dict[str, type[Backend]] = {
    "codex": CodexBackend,
    "copilot": CopilotBackend,
    "claude": ClaudeBackend,
}

def get_backend(engine: str, **kwargs) -> Backend:
    """Instantiate a backend by engine name. Raises KeyError if unknown."""
    cls = BACKEND_REGISTRY[engine]
    return cls(**kwargs)
```

### `backends/sandbox.py` — Docker Sandbox Decorator

```python
class SandboxBackend:
    """Wraps any Backend to execute inside a Docker container.
    
    Used in uvx path with --sandbox docker flag.
    Maps engine name to Docker image: codex → ralph-loop-codex, etc.
    """
    
    def __init__(self, inner: Backend, auth_config: AuthConfig, workspace_dir: str):
        self.inner = inner
        self.auth = auth_config
        self.workspace = workspace_dir
    
    def execute(self, prompt, model=None, timeout_seconds=600, extra_flags=None, cwd=None):
        image = f"ralph-loop-{self.inner.name}"
        
        # Write prompt to .ralph-tmp/ for container access
        prompt_file = self._write_prompt_file(prompt)
        
        cmd = ["docker", "run", "--rm"]
        cmd.extend(["-v", f"{self.workspace}:/workspace"])
        
        # Mount auth directories
        for mount_path in self.auth.mount:
            expanded = os.path.expanduser(mount_path)
            cmd.extend(["-v", f"{expanded}:{expanded}"])
        
        # Forward env vars
        for env_var in self.auth.env:
            if env_var in os.environ:
                cmd.extend(["-e", env_var])
        
        cmd.extend([image, "execute", "--prompt-file", f"/workspace/{prompt_file}"])
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_seconds)
        ...
```

---

## `.ralph-tmp/` Communication Protocol

The bash script and containers communicate via files in `.ralph-tmp/` (inside the mounted workspace). This avoids piping JSON through Docker and makes everything inspectable/debuggable.

**File inventory:**

| File | Writer | Reader | Format | Purpose |
|---|---|---|---|---|
| `.ralph-tmp/next-action.json` | base container | bash script | JSON (NextAction) | What to do next |
| `.ralph-tmp/coder-prompt.md` | base container | CLI container | Markdown | Prompt for coder |
| `.ralph-tmp/inspector-prompt.md` | base container | CLI container | Markdown | Prompt for inspector |
| `.ralph-tmp/step-result.json` | bash script | base container | JSON (StepResult) | Result of last step |
| `.ralph-tmp/iteration-state.json` | base container | base container | JSON | Tracks position within iteration |
| `.ralph-tmp/git-diff.patch` | base container | base container | Unified diff | Diff captured before inspection |

**`next-action.json` schemas (one per command type):**

```json
// command: "code"
{
  "command": "code",
  "task_id": "01",
  "image": "ralph-loop-codex",
  "prompt_file": ".ralph-tmp/coder-prompt.md",
  "model": "gpt-5.3-codex",
  "timeout_seconds": 600,
  "auth": {
    "env": ["OPENAI_API_KEY"],
    "mount": []
  }
}

// command: "verify"
{
  "command": "verify",
  "task_id": "01",
  "commands": ["./bin/run-tests tests/path/to/test.py"]
}

// command: "inspect"  
{
  "command": "inspect",
  "task_id": "01",
  "image": "ralph-loop-copilot",
  "prompt_file": ".ralph-tmp/inspector-prompt.md",
  "model": "claude-opus-4-6",
  "timeout_seconds": 300,
  "auth": {
    "env": [],
    "mount": ["~/.config/gh"]
  }
}

// command: "update"
{
  "command": "update",
  "task_id": "01"
}

// command: "done"
{
  "command": "done",
  "reason": "all_completed",
  "summary": {"completed": 5, "aborted": 0, "total": 5}
}

// command: "abort"  
{
  "command": "abort",
  "task_id": "01",
  "reason": "max_retries_exceeded",
  "retries": 3
}

// command: "pause"
{
  "command": "pause",
  "reason": "PAUSE.md found"
}
```

**`step-result.json` schema:**

```json
// After code step
{
  "step": "code",
  "task_id": "01",
  "exit_code": 0,
  "duration_seconds": 45.2
}

// After verify step
{
  "step": "verify",
  "task_id": "01",
  "results": [
    {
      "command": "./bin/run-tests tests/path/to/test.py",
      "exit_code": 1,
      "stdout": "... truncated ...",
      "stderr": "FAILED test_happy_path ..."
    }
  ]
}

// After inspect step
{
  "step": "inspect",
  "task_id": "01",
  "exit_code": 0,
  "stdout": "{ \"verdict\": \"pass\", \"feedback\": \"...\" }"
}
```

**`iteration-state.json`** — tracks where we are within a single task iteration:

```json
{
  "task_id": "01",
  "current_step": "verify",
  "steps_completed": ["code"],
  "started_at": "2026-03-01T14:30:00Z"
}
```

This file is read by the `next-action` command to determine what step comes next for the current task, rather than re-selecting a task.

---

## Host Bash Orchestrator — Full Specification

The `ralph-loop` bash script is the primary user-facing entry point. It:
1. Checks Docker is available
2. Auto-builds missing images
3. Sets up `.ralph-tmp/` directory
4. Runs the step-by-step command loop
5. Handles signals (Ctrl+C), cleanup, and exit codes

```bash
#!/bin/bash
# ralph-loop — Host orchestrator for AI coding loops
# Usage: ./ralph-loop <command> [options]
#   CONFIG=~/.config/ralph-loop/config.yaml LOOP_DIR=.ralph-loop/my-loop ./ralph-loop run
#   CONFIG=~/.config/ralph-loop/config.yaml LOOP_DIR=.ralph-loop/my-loop ./ralph-loop status
#   ./ralph-loop init   --from plan.md
# 
# This script dispatches work to Docker containers.
# It is deliberately "dumb" — all logic lives in ralph-loop-base (Python).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RALPH_TMP=".ralph-tmp"
CONFIG="${CONFIG:-ralph-config.yaml}"
LOOP_DIR="${LOOP_DIR:-.}"
DOCKER_USER="$(id -u):$(id -g)"

# ── Color output ──
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[ralph]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[ralph]${NC} $*"; }
log_error() { echo -e "${RED}[ralph]${NC} $*" >&2; }

# ── Cleanup on exit ──
cleanup() {
    log_info "Cleaning up .ralph-tmp/..."
    rm -rf "$RALPH_TMP"
    exit "${1:-0}"
}
trap 'cleanup 130' INT TERM

# ── Docker image management ──
ensure_image() {
    local target="$1"
    local image="ralph-loop-${target}"
    if ! docker image inspect "$image" &>/dev/null; then
        log_info "Building $image..."
        docker build --target "$target" -t "$image" "$SCRIPT_DIR" --quiet
    fi
}

# ── Run a base container command ──
run_base() {
    docker run --rm \
        -u "$DOCKER_USER" \
        -v "$PWD:/workspace" \
        ralph-loop-base "$@"
}

# ── Run a CLI container ──
run_cli() {
    local image="$1"; shift
    local auth_env=() auth_mount=()
    
    # Parse auth from next-action.json
    while IFS= read -r env_var; do
        [[ -n "${!env_var:-}" ]] && auth_env+=("-e" "$env_var")
    done < <(jq -r '.auth.env[]? // empty' "$RALPH_TMP/next-action.json")
    
    while IFS= read -r mount_path; do
        expanded="${mount_path/#\~/$HOME}"
        [[ -d "$expanded" ]] && auth_mount+=("-v" "${expanded}:${expanded}")
    done < <(jq -r '.auth.mount[]? // empty' "$RALPH_TMP/next-action.json")
    
    docker run --rm \
        -u "$DOCKER_USER" \
        -v "$PWD:/workspace" \
        "${auth_env[@]}" \
        "${auth_mount[@]}" \
        "$image" "$@"
}

# ── Run one trusted verify command in isolated shell ──
run_trusted_verify_cmd() {
    local cmd="$1"
    # Intentionally supports shell syntax (pipes, redirects, &&, env assignments)
    # while avoiding eval side effects in the parent shell.
    bash -lc "$cmd"
}

# ── Commands ──
cmd_run() {
    # Ensure required images exist
    ensure_image "base"
    
    # Detect which CLI images are needed from config
    for engine in $(run_base list-engines --config "/workspace/$CONFIG" 2>/dev/null); do
        ensure_image "$engine"
    done
    
    mkdir -p "$RALPH_TMP"
    
    log_info "Starting ralph loop..."
    
    while true; do
        # Step 1: Ask the brain what to do
        run_base next-action \
            --config "/workspace/$CONFIG" \
            --step-result "/workspace/$RALPH_TMP/step-result.json" \
            > "$RALPH_TMP/next-action.json"
        
        COMMAND=$(jq -r '.command' "$RALPH_TMP/next-action.json")
        TASK_ID=$(jq -r '.task_id // "n/a"' "$RALPH_TMP/next-action.json")
        
        case "$COMMAND" in
            code)
                IMAGE=$(jq -r '.image' "$RALPH_TMP/next-action.json")
                TIMEOUT=$(jq -r '.timeout_seconds // 600' "$RALPH_TMP/next-action.json")
                log_info "Task $TASK_ID: coding via $IMAGE..."
                
                set +e
                SECONDS=0
                run_cli "$IMAGE" execute \
                    --prompt-file "/workspace/$(jq -r '.prompt_file' "$RALPH_TMP/next-action.json")"
                EXIT_CODE=$?
                DURATION=$SECONDS
                set -e
                
                # Write step result
                jq -n --arg task "$TASK_ID" --argjson exit "$EXIT_CODE" --argjson dur "$DURATION" \
                    '{step:"code", task_id:$task, exit_code:$exit, duration_seconds:$dur}' \
                    > "$RALPH_TMP/step-result.json"
                ;;
            
            verify)
                log_info "Task $TASK_ID: running verification on host..."
                RESULTS="[]"
                
                while IFS= read -r cmd; do
                    set +e
                    OUTPUT=$(run_trusted_verify_cmd "$cmd" 2>&1)
                    CMD_EXIT=$?
                    set -e
                    
                    # Truncate output to 4000 chars
                    TRUNCATED=$(echo "$OUTPUT" | tail -c 4000)
                    
                    RESULTS=$(echo "$RESULTS" | jq \
                        --arg cmd "$cmd" \
                        --argjson exit "$CMD_EXIT" \
                        --arg out "$TRUNCATED" \
                        '. + [{command:$cmd, exit_code:$exit, stdout:$out, stderr:""}]')
                    
                    log_info "  Command: $cmd → exit $CMD_EXIT"
                done < <(jq -r '.commands[]' "$RALPH_TMP/next-action.json")
                
                jq -n --arg task "$TASK_ID" --argjson results "$RESULTS" \
                    '{step:"verify", task_id:$task, results:$results}' \
                    > "$RALPH_TMP/step-result.json"
                ;;
            
            inspect)
                IMAGE=$(jq -r '.image' "$RALPH_TMP/next-action.json")
                log_info "Task $TASK_ID: AI inspection via $IMAGE..."
                
                set +e
                STDOUT=$(run_cli "$IMAGE" inspect \
                    --prompt-file "/workspace/$(jq -r '.prompt_file' "$RALPH_TMP/next-action.json")")
                EXIT_CODE=$?
                set -e
                
                jq -n --arg task "$TASK_ID" --argjson exit "$EXIT_CODE" --arg out "$STDOUT" \
                    '{step:"inspect", task_id:$task, exit_code:$exit, stdout:$out}' \
                    > "$RALPH_TMP/step-result.json"
                ;;
            
            update)
                log_info "Task $TASK_ID: updating progress..."
                run_base update \
                    --config "/workspace/$CONFIG" \
                    --result-dir "/workspace/$RALPH_TMP/"
                ;;
            
            done)
                REASON=$(jq -r '.reason' "$RALPH_TMP/next-action.json")
                log_info "✅ Loop complete: $REASON"
                cleanup 0
                ;;
            
            abort)
                REASON=$(jq -r '.reason' "$RALPH_TMP/next-action.json")
                log_error "❌ Task $TASK_ID aborted: $REASON"
                # Continue loop — other tasks may still be workable
                ;;
            
            pause)
                log_warn "⏸  Paused — remove PAUSE.md to resume."
                sleep 30
                ;;
            
            *)
                log_error "Unknown command: $COMMAND"
                cleanup 1
                ;;
        esac
    done
}

cmd_status() {
    ensure_image "base"
    run_base status --config "/workspace/$CONFIG"
}

cmd_init() {
    ensure_image "base"
    run_base init "$@"
}

cmd_validate() {
    ensure_image "base"
    run_base validate --config "/workspace/$CONFIG"
}

cmd_build_images() {
    for target in base codex copilot claude; do
        ensure_image "$target"
    done
    log_info "All images built."
}

# ── Main dispatch ──
SUBCOMMAND="${1:-help}"; shift || true
case "$SUBCOMMAND" in
    run)           cmd_run "$@" ;;
    status)        cmd_status "$@" ;;
    init)          cmd_init "$@" ;;
    validate)      cmd_validate "$@" ;;
    build-images)  cmd_build_images "$@" ;;
    help|--help|-h)
        echo "Usage: ralph-loop <command> [options]"
        echo ""
        echo "Commands:"
        echo "  run           Start the main loop"
        echo "  status        Show progress summary"
        echo "  init          Generate tasks from a plan document"
        echo "  validate      Validate config + progress"
        echo "  build-images  Pre-build all Docker images"
        ;;
    *)
        log_error "Unknown command: $SUBCOMMAND"
        exit 1
        ;;
esac
```

## Multi-Model Strategy Per Task Phase

| Phase | Backend Engine | Model | Purpose |
|---|---|---|---|
| Coding | Codex | gpt-5.3-codex | Implementation (code + tests) |
| AI Inspection | Copilot | Claude Opus 4.6 | Review git diff vs acceptance criteria |
| Visual Verification | Copilot | Claude Opus 4.6 | Screenshot comparison (optional) |
| Test Verification | HOST | N/A | User-configured deterministic commands |

These are defaults from `ralph-config.yaml`. The user can reconfigure any role to any backend/model.

---

## `next-action` Command Logic (Brain State Machine)

The `next-action` CLI command is the core brain. It reads PROGRESS.yaml + iteration state + step results to determine what the bash script should do next.

**Algorithm:**

```python
def next_action(config: RalphConfig, step_result: StepResult | None) -> NextAction:
    progress = load_progress(config.progress_file)
    
    # 1. Check pause file
    if Path(config.pause_file).exists():
        return NextAction(command="pause", reason="PAUSE.md found")
    
    # 2. Load iteration state (are we mid-task?)
    iteration = load_iteration_state()  # from .ralph-tmp/iteration-state.json
    
    if iteration and step_result:
        # We're mid-task. Determine next step based on what just completed.
        task = find_task(progress, iteration.task_id)
        
        if step_result.step == "code":
            # Code done → now verify
            verify_cmds = task.verify_commands or config.verify_commands
            if verify_cmds:
                save_iteration_state(task_id=iteration.task_id, current_step="verify")
                return NextAction(command="verify", task_id=task.id, commands=verify_cmds)
            else:
                # No verify commands → skip to inspect
                diff = capture_git_diff()
                write_inspector_prompt(task, diff, progress)
                save_iteration_state(task_id=iteration.task_id, current_step="inspect")
                return NextAction(command="inspect", ...)
        
        elif step_result.step == "verify":
            # Verify done → now inspect (always, regardless of verify outcome)
            diff = capture_git_diff()
            write_inspector_prompt(task, diff, progress, verify_results=step_result.results)
            save_iteration_state(task_id=iteration.task_id, current_step="inspect")
            return NextAction(command="inspect", ...)
        
        elif step_result.step == "inspect":
            # Inspect done → update progress
            save_iteration_state(task_id=iteration.task_id, current_step="update")
            return NextAction(command="update", task_id=task.id)
    
    if iteration and iteration.current_step == "update":
        # Update was the last step → clear iteration state, loop back
        clear_iteration_state()
        # Fall through to task selection below
    
    # 3. No active iteration → select next task
    task = select_next_task(progress, config.max_retries)
    
    if task is None:
        # All done or all aborted
        summary = compute_summary(progress)
        if summary.all_completed:
            return NextAction(command="done", reason="all_completed", summary=summary)
        else:
            return NextAction(command="done", reason="remaining_tasks_aborted", summary=summary)
    
    # 4. Lock task and start coding
    lock_task(progress, task.id)
    save_progress(progress, config.progress_file)
    
    task_data = Task.load(task.task_file)
    write_coder_prompt(task_data, task.feedback, config)
    save_iteration_state(task_id=task.id, current_step="code")
    
    backend = config.get_backend("coder")
    auth = config.get_auth(backend.engine)
    return NextAction(
        command="code",
        task_id=task.id,
        image=f"ralph-loop-{backend.engine}",
        prompt_file=".ralph-tmp/coder-prompt.md",
        model=backend.model,
        timeout_seconds=backend.timeout_seconds,
        auth=auth,
    )
```

**The `update` command logic:**

```python
def update(config: RalphConfig, result_dir: str) -> None:
    progress = load_progress(config.progress_file)
    iteration = load_iteration_state()
    task = find_task(progress, iteration.task_id)
    
    # Collect all step results from result_dir
    all_results = collect_step_results(result_dir)
    
    # Determine overall verdict
    all_passed = True
    feedback_sources = []
    
    for result in all_results:
        if result.step == "verify":
            for cmd_result in result.results:
                verdict = "pass" if cmd_result.exit_code == 0 else "fail"
                all_passed = all_passed and (verdict == "pass")
                feedback_sources.append(FeedbackSource(
                    type="test",
                    verdict=verdict,
                    command=cmd_result.command,
                    exit_code=cmd_result.exit_code,
                    output=truncate_output(cmd_result.stdout),
                ))
        
        elif result.step == "inspect":
            inspection = parse_inspector_verdict(result.stdout)
            all_passed = all_passed and (inspection.verdict == "pass")
            feedback_sources.append(FeedbackSource(
                type="ai_inspection",
                verdict=inspection.verdict,
                details=inspection.feedback,
            ))
    
    if all_passed:
        complete_task(progress, task.id)
    else:
        entry = FeedbackEntry(
            attempt=task.retries + 1,
            timestamp=datetime.utcnow().isoformat() + "Z",
            sources=feedback_sources,
        )
        fail_task(progress, task.id, entry, config.max_retries)
    
    save_progress(progress, config.progress_file)
```

---

## AI CLI Reference (Headless One-Liners)

| Tool | Fully Autonomous Non-Interactive Command |
|---|---|
| **Codex** | `codex exec --dangerously-bypass-approvals-and-sandbox -m <model> "prompt"` |
| **Copilot** | `copilot -p --allow-all-tools --model <model> "prompt"` |
| **Claude Code** | `claude -p --dangerously-skip-permissions --max-turns 20 --model <model> "prompt"` |

| Feature | Codex | Copilot | Claude Code |
|---|---|---|---|
| Non-interactive flag | `-q` / `exec` | `-p` | `-p` (print) |
| Full auto-approve | `--dangerously-bypass-approvals-and-sandbox` | `--allow-all-tools` | `--dangerously-skip-permissions` |
| Model selection | `-m <model>` | `--model <model>` | `--model <model>` |
| JSON output | `--json` | Not yet available | `--output-format json` |
| Turn limit | None (use shell `timeout`) | Not yet available | `--max-turns N` |
| Budget cap | None | Not available | `--max-budget-usd` |
| Instructions file | `AGENTS.md` | Custom instructions + Memory | `CLAUDE.md` + `.claude/rules/` |

## Configuration: `ralph-config.yaml`

Full schema with all fields and types:

```yaml
# ── Required fields ──
progress_file: PROGRESS.yaml           # Path to progress tracker (relative to workspace)
task_dir: tasks/                        # Directory containing task .md files

backends:                               # At minimum, "coder" must be defined
  coder:
    engine: codex                       # "codex" | "copilot" | "claude"
    model: gpt-5.3-codex               # Model name (null = CLI default)
    extra_flags: []                     # Additional CLI args
    timeout_seconds: 600                # Shell timeout per invocation
  inspector:
    engine: copilot
    model: claude-opus-4-6
    timeout_seconds: 300
  visual:                               # Optional — only needed if tasks have visual_verify
    engine: copilot
    model: claude-opus-4-6
    timeout_seconds: 300

# ── Optional fields (with defaults shown) ──
max_retries: 3                          # Max retry attempts per task before abort
pause_file: PAUSE.md                    # If this file exists, loop pauses
workspace_dir: "."                      # Resolved at runtime

verify_commands:                        # Default commands run for all tasks
  - "./bin/run-tests tests/"            # Tasks can override via frontmatter

project_instructions: null              # Path to AGENTS.md/CLAUDE.md (injected into coder prompt)

auth:                                   # Credential forwarding for Docker containers
  codex:
    env: [OPENAI_API_KEY]               # Env vars forwarded to container
    mount: []                           # Host dirs mounted into backend containers
  copilot:
    env: []
    mount: ["~/.config/gh"]
  claude:
    env: [ANTHROPIC_API_KEY]
    mount: ["~/.claude"]
```

## PROGRESS.yaml Structure

```yaml
meta:
  title: "Project Name"
  started: "2026-03-01"
  current_phase: 1

phases:
  - id: 1
    name: "Phase Name"
    status: in_progress  # not_started | in_progress | completed
    tasks:
      - id: "01"
        title: "Task Title"
        task_file: "tasks/01-task-slug.md"
        contract_file: null
        status: not_started  # not_started | in_progress | completed | failed | abort
        retries: 0
        verify_commands:
          - "./bin/run-tests tests/path/to/test.py"
        visual_verify: null
        feedback: []
```

## Task File Format (`.md` with YAML frontmatter)

```markdown
---
phase: 1
priority: high
verify_commands:
  - "./bin/run-tests tests/path/to/test.py"
visual_verify: null
contract_file: null
files_to_touch:
  - "src/module/file.py"
files_not_to_touch:
  - "src/core/unrelated.py"
---

# Task 01: Task Title

**Phase**: 1 — Phase Name
**Priority**: High

## Description

What to implement.

## Acceptance Criteria

1. First criterion (testable, specific)
2. Second criterion
3. ...

## Files to Create/Modify

- `src/module/file.py` — description of changes

## Test Plan

File: `tests/path/to/test.py`

1. Test happy path
2. Test validation errors
3. Test not-found cases
4. Test edge cases

## Reference Implementation

See `src/existing/pattern.py` for the exact pattern to follow.

## Constraints

- Do NOT modify `src/core/unrelated.py`
- Follow existing patterns in `src/module/`
```

## Task Lifecycle (One Iteration)

```
Host bash              Base container              CLI container
─────────              ──────────────              ─────────────
next-action ─────────→ check PAUSE.md
                       select task (not_started or failed < max_retries)
                       build coder prompt (task + accumulated feedback)
                       write .ralph-tmp/coder-prompt.md
             ←──────── {"command":"code","image":"ralph-loop-codex",...}

docker run codex ──────────────────────────────→ codex exec "prompt"
                                                  writes code to /workspace
             ←──────────────────────────────────  exit_code

next-action ─────────→ "code step done, now verify"
             ←──────── {"command":"verify","commands":["./bin/run-tests ..."]}

bash -lc "./bin/run-tests"  (runs on HOST)
capture stdout/stderr/exit_code → .ralph-tmp/verify-result.json

next-action ─────────→ "verify done, build inspect prompt"
                       capture git diff
                       write .ralph-tmp/inspector-prompt.md
             ←──────── {"command":"inspect","image":"ralph-loop-copilot",...}

docker run copilot ─────────────────────────────→ copilot -p "inspect diff"
                                                   returns verdict
             ←──────────────────────────────────  stdout (verdict)

next-action ─────────→ "inspect done, aggregate results"
             ←──────── {"command":"update"}

update ──────────────→ parse all results
                       ALL passed → status: completed
                       ANY failed → accumulate feedback, retries++
                       retries >= max → status: abort
                       write PROGRESS.yaml
             ←──────── {"command":"continue"} (loop continues)
```

## Feedback Accumulation

Each failed attempt produces a `FeedbackEntry`:

```yaml
feedback:
  - attempt: 1
    timestamp: "2026-03-01T14:32:00Z"
    sources:
      - type: test
        command: "./bin/run-tests tests/path/to/test.py"
        exit_code: 1
        output: "FAILED test_happy_path - AssertionError: expected 200 got 422"
      - type: ai_inspection
        verdict: fail
        details: "Missing error handling for empty input. Acceptance criterion #3 not implemented."
      - type: visual
        verdict: pass
        details: null
```

On retry, ALL accumulated feedback is injected into the coder prompt so the AI sees every failure from all previous attempts.

**Critical rule:** run ALL verification steps on every attempt (even if one fails early) to collect maximum feedback per retry. Max 3 retries, then abort.

## Verification Pipeline

1. **Deterministic** (`verify_commands`): run ALL commands on HOST, collect all stdout/stderr/exit_codes. Do NOT stop on first failure.
2. **Visual** (optional, `visual_verify`): take screenshot via Playwright/MCP and validate with AI backend (with or without reference image).
3. **AI Inspection** (always): capture `git diff`, build inspection prompt with diff + acceptance criteria + contract + previous feedback, execute via inspector backend, parse pass/fail verdict.
4. **Aggregate**: all pass → PASS; any fail → FAIL with accumulated feedback entries.

### Inspector Verdict Parsing

The AI inspector is instructed (via the prompt template) to output a JSON block:

```json
{
  "verdict": "pass",
  "feedback": "All acceptance criteria met. Implementation follows existing patterns."
}
```

or:

```json
{
  "verdict": "fail",
  "feedback": "Criterion #3 not implemented: missing error handling for empty input.\nCriterion #5 partially done: endpoint returns 200 but response schema is wrong."
}
```

The `update` command parses this without regex:

1. Attempt strict `json.loads(stdout)`.
2. If parsing fails, call a normalization LLM prompt that converts raw inspector output to strict JSON with this schema:

```json
{
    "verdict": "fail",
    "feedback": "string"
}
```

Where `verdict` is restricted to the enum values `"pass"` or `"fail"`.

3. Validate normalized output with Pydantic.
4. If validation still fails, treat entire stdout as feedback with verdict `"fail"` (defensive).

---

## Jinja Prompt Template Variable Contracts

### `prompts/coder.md.j2` — Variables

| Variable | Type | Description |
|---|---|---|
| `task.title` | `str` | Task title from .md file |
| `task.description` | `str` | Description section content |
| `task.acceptance_criteria` | `list[str]` | Numbered criteria |
| `task.test_plan` | `str` | Raw test plan markdown section |
| `task.reference_impl` | `str \| None` | Reference implementation pointers |
| `task.constraints` | `list[str]` | Constraints (do NOT touch, etc.) |
| `task.files_to_touch` | `list[str]` | Files to create/modify |
| `task.files_not_to_touch` | `list[str]` | Files NOT to modify |
| `task.body` | `str` | Full task markdown body (fallback) |
| `contract_content` | `str \| None` | Content of contract_file if referenced |
| `project_instructions` | `str \| None` | Content of AGENTS.md/CLAUDE.md |
| `previous_feedback` | `str \| None` | Formatted feedback from all previous attempts (via `format_feedback_for_prompt()`) |
| `is_retry` | `bool` | True if this is a retry (retries > 0) |
| `attempt_number` | `int` | Current attempt number (1-based) |

**Template skeleton:**

```markdown
# Task: {{ task.title }}

## Description
{{ task.description }}

## Acceptance Criteria
{% for criterion in task.acceptance_criteria %}
{{ loop.index }}. {{ criterion }}
{% endfor %}

## Files to Create/Modify
{% for f in task.files_to_touch %}
- `{{ f }}`
{% endfor %}

## Test Plan
{{ task.test_plan }}

{% if task.reference_impl %}
## Reference Implementation
{{ task.reference_impl }}
{% endif %}

## Constraints
{% for c in task.constraints %}
- {{ c }}
{% endfor %}
{% for f in task.files_not_to_touch %}
- Do NOT modify `{{ f }}`
{% endfor %}

{% if contract_content %}
## Implementation Contract
{{ contract_content }}
{% endif %}

{% if project_instructions %}
## Project Instructions
{{ project_instructions }}
{% endif %}

{% if is_retry %}
## ⚠️ RETRY — Attempt {{ attempt_number }} of {{ max_retries }}

This task FAILED on previous attempts. Review the feedback below carefully.
Fix ALL issues mentioned before declaring the task complete.

{{ previous_feedback }}
{% endif %}

## Instructions
1. Implement the task end-to-end, including tests.
2. Run the test commands to verify your work before declaring done.
3. Commit your changes with a conventional commit message.
4. If this is a retry, focus on fixing the specific issues listed in the feedback.
```

### `prompts/inspector.md.j2` — Variables

| Variable | Type | Description |
|---|---|---|
| `task.title` | `str` | Task title |
| `task.acceptance_criteria` | `list[str]` | Criteria to verify against |
| `task.test_plan` | `str` | Expected test coverage |
| `git_diff` | `str` | Unified diff of all changes |
| `verify_results` | `list[dict] \| None` | Deterministic test results (command, exit_code, output) |
| `contract_content` | `str \| None` | Implementation contract if present |
| `project_instructions` | `str \| None` | AGENTS.md content |

**Template skeleton:**

```markdown
# Code Review: {{ task.title }}

You are a strict code reviewer. Review the following changes against the acceptance criteria.

## Acceptance Criteria
{% for criterion in task.acceptance_criteria %}
{{ loop.index }}. {{ criterion }}
{% endfor %}

## Changes (git diff)
```diff
{{ git_diff }}
```

{% if verify_results %}
## Deterministic Test Results
{% for r in verify_results %}
### Command: `{{ r.command }}`
- Exit code: {{ r.exit_code }}
- Output:
```
{{ r.stdout }}
```
{% endfor %}
{% endif %}

{% if contract_content %}
## Implementation Contract
{{ contract_content }}
{% endif %}

## Your Task

Review the diff and verify:
1. Every acceptance criterion is fully implemented (not partial, no placeholders).
2. Tests have been added and cover the acceptance criteria.
3. Code follows project standards (clean, no TODOs, documented).
4. No unintended changes outside the task scope.

Respond with EXACTLY this JSON format (no other text before or after):

```json
{
  "verdict": "pass",
  "feedback": "Brief explanation of your verdict."
}
```

Use `"verdict": "pass"` if ALL criteria are met.
Use `"verdict": "fail"` if ANY criterion is not met, and explain what's missing/wrong in the feedback field.
```

### `prompts/plan_to_tasks.md.j2` — Variables

| Variable | Type | Description |
|---|---|---|
| `source_content` | `str` | Raw content of the plan/PRD/design document |
| `project_instructions` | `str \| None` | AGENTS.md content |
| `task_format_example` | `str` | Complete example of a task .md file |
| `progress_schema_example` | `str` | Complete example PROGRESS.yaml |

---

## Native Loop Orchestrator — `loop.py`

`loop.py` is the Python-native orchestrator for the `uvx ralph-loop run` path. It implements the same loop as the bash script but entirely in Python (no Docker dispatch, just subprocess calls).

```python
from __future__ import annotations
import signal
import sys
from pathlib import Path

from ralph_loop.config import RalphConfig
from ralph_loop.progress import (
    load_progress, save_progress, select_next_task, lock_task,
    complete_task, fail_task, find_task, advance_phase,
)
from ralph_loop.task import Task
from ralph_loop.feedback import FeedbackEntry, FeedbackSource, format_feedback_for_prompt
from ralph_loop.backends import get_backend
from ralph_loop.backends.sandbox import SandboxBackend
from ralph_loop.verification.pipeline import run_verification_pipeline


def run_loop(config: RalphConfig, sandbox: str = "none") -> int:
    """Run the main orchestration loop natively.

    Args:
        config: Loaded RalphConfig.
        sandbox: "none" for direct subprocess, "docker" to wrap in SandboxBackend.

    Returns:
        Exit code: 0 if all tasks completed, 1 if any aborted.
    """
    _setup_signal_handlers()

    while True:
        progress = load_progress(config.progress_file)

        # Check pause file
        if Path(config.pause_file).exists():
            print("[ralph] Paused — remove PAUSE.md to resume.")
            import time; time.sleep(30)
            continue

        task_progress = select_next_task(progress, config.max_retries)
        if task_progress is None:
            # All done
            _print_summary(progress)
            return 0 if _all_completed(progress) else 1

        # Lock and save
        lock_task(progress, task_progress.id)
        save_progress(progress, config.progress_file)

        # Load full task data
        task = Task.load(task_progress.task_file)

        # Build coder backend
        coder_cfg = config.get_backend("coder")
        coder = _make_backend(coder_cfg, config, sandbox)

        # Build coder prompt
        feedback_text = format_feedback_for_prompt(task_progress.feedback)
        prompt = _render_coder_prompt(task, feedback_text, config, task_progress)

        # Execute coding
        print(f"[ralph] Task {task_progress.id}: coding via {coder_cfg.engine}...")
        code_result = coder.execute(
            prompt=prompt,
            model=coder_cfg.model,
            timeout_seconds=coder_cfg.timeout_seconds,
            extra_flags=coder_cfg.extra_flags,
        )

        # Run verification pipeline (deterministic + AI inspection)
        inspector_cfg = config.get_backend("inspector")
        inspector = _make_backend(inspector_cfg, config, sandbox)
        verify_cmds = task.get_verify_commands(config.verify_commands)

        report = run_verification_pipeline(
            task=task,
            task_progress=task_progress,
            config=config,
            inspector_backend=inspector,
            verify_commands=verify_cmds,
        )

        # Update progress based on results
        progress = load_progress(config.progress_file)  # Re-read for freshness
        if report.all_passed:
            complete_task(progress, task_progress.id)
            print(f"[ralph] Task {task_progress.id}: ✅ COMPLETED")
        else:
            entry = FeedbackEntry(
                attempt=task_progress.retries + 1,
                timestamp=_now_iso(),
                sources=report.feedback_sources,
            )
            fail_task(progress, task_progress.id, entry, config.max_retries)
            new_status = find_task(progress, task_progress.id).status
            print(f"[ralph] Task {task_progress.id}: ❌ {new_status.value}")

        save_progress(progress, config.progress_file)
        advance_phase(progress)
        save_progress(progress, config.progress_file)


def _make_backend(cfg, config, sandbox):
    """Instantiate backend, optionally wrapped in SandboxBackend."""
    backend = get_backend(cfg.engine)
    if sandbox == "docker":
        auth = config.get_auth(cfg.engine)
        return SandboxBackend(backend, auth, config.workspace_dir)
    return backend


def _setup_signal_handlers():
    """Graceful shutdown on SIGINT/SIGTERM."""
    def handler(sig, frame):
        print("\n[ralph] Interrupted. Exiting...")
        sys.exit(130)
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
```

**Key differences from bash orchestrator:**
- No `.ralph-tmp/` files — calls are in-process via Python objects
- No Docker dispatch — backends call subprocess directly (or SandboxBackend wraps in Docker)
- Same verification pipeline — `run_verification_pipeline()` is shared code
- Same state machine — uses identical `progress.py` functions

---

## CLI Commands (Detailed Specification)

All CLI commands are implemented in `ralph_loop/cli.py` using Click.

### `ralph-loop next-action`

**Purpose:** Brain command — determine what the bash script should do next.

```
ralph-loop next-action --config PATH [--step-result PATH]
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `--config` | Path | Yes | Path to ralph-config.yaml |
| `--step-result` | Path | No | Path to step-result.json from previous step |

**Output:** Writes JSON to stdout (captured by bash into `.ralph-tmp/next-action.json`).
**Side effects:** May write prompt files to `.ralph-tmp/`, update PROGRESS.yaml (lock task), write iteration-state.json.

### `ralph-loop update`

**Purpose:** Process all step results and update PROGRESS.yaml.

```
ralph-loop update --config PATH --result-dir PATH
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `--config` | Path | Yes | Path to ralph-config.yaml |
| `--result-dir` | Path | Yes | Directory containing step-result.json files |

**Output:** None (updates PROGRESS.yaml directly).
**Side effects:** Updates task status, appends feedback, may upgrade to abort. Clears iteration-state.json.

### `ralph-loop execute`

**Purpose:** Run an AI CLI prompt (called inside CLI containers).

```
ralph-loop execute --prompt-file PATH
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `--prompt-file` | Path | Yes | Path to the prompt .md file |

**Behavior:** Reads prompt file, determines which AI CLI is available in the container, calls it. Propagates exit code.

### `ralph-loop inspect`

**Purpose:** Run an AI CLI prompt for inspection (called inside CLI containers).

```
ralph-loop inspect --prompt-file PATH
```

Same as `execute` but captures stdout for verdict parsing.

### `ralph-loop run`

**Purpose:** Native loop for uvx path (not used in Docker-primary mode).

```
ralph-loop run --config PATH [--sandbox docker|none]
```

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `--config` | Path | No | `ralph-config.yaml` | Config file path |
| `--sandbox` | Choice | No | `none` | Wrap CLI calls in Docker containers |

**Behavior:** Runs the full loop natively in Python. Calls backends via subprocess (or SandboxBackend if `--sandbox docker`). Runs verify_commands via subprocess on host.

### `ralph-loop status`

**Purpose:** Print a summary of PROGRESS.yaml.

```
ralph-loop status --config PATH
```

**Output example:**
```
ralph-loop status
─────────────────────────────────
Phase 1: Data Models (COMPLETED)
  ✅ 01 - AdvisorDossierData model
  ✅ 02 - Repository implementation

Phase 2: Service Layer (IN PROGRESS)
  ✅ 03 - AdvisorCatalogService
  🔄 04 - Filtering logic (attempt 2/3)
  ⬜ 05 - Cache integration

Phase 3: API Router (NOT STARTED)
  ⬜ 06 - Router endpoints
  ⬜ 07 - OpenAPI schema
─────────────────────────────────
Progress: 3/7 completed, 1 in progress, 3 not started
```

### `ralph-loop reset`

**Purpose:** Reset a task to `not_started` state.

```
ralph-loop reset TASK_ID --config PATH
```

Clears task status, retries count, and feedback array.

### `ralph-loop validate`

**Purpose:** Validate consistency between config, progress, and task files.

```
ralph-loop validate --config PATH
```

**Checks:**
- Config YAML is valid and all required fields present
- PROGRESS.yaml parses correctly
- Every task referenced in PROGRESS.yaml has a corresponding .md file
- Every .md file in task_dir is referenced in PROGRESS.yaml
- Backend engines are known (codex, copilot, claude)
- Auth config exists for each configured engine

### `ralph-loop init`

**Purpose:** Generate tasks + PROGRESS.yaml from a plan document.

```
ralph-loop init --from PATH [--backend ENGINE] [--model MODEL] [--config PATH]
```

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `--from` | Path | Yes | — | Source plan document |
| `--backend` | str | No | inspector engine from config | Which AI backend to use for conversion |
| `--model` | str | No | inspector model from config | Model override |
| `--config` | Path | No | `ralph-config.yaml` | Config file (generates scaffold if missing) |

**Behavior:** Reads source document, renders `plan_to_tasks.md.j2` template, calls AI backend, parses structured output, writes task files + PROGRESS.yaml.

### `ralph-loop list-engines`

**Purpose:** List engine names from config (used by bash script to know which images to build).

```
ralph-loop list-engines --config PATH
```

**Output:** One engine name per line (e.g., `codex\ncopilot`). Deduplicates across roles.

### `ralph-loop build-images`

**Purpose:** Build all Docker images.

```
ralph-loop build-images [--only ENGINE]
```

Not a Python CLI command — handled by the bash script directly.

## Plan-to-Tasks Converter

`ralph-loop init --from <plan>` transforms any plan document into ralph-loop tasks.

**Input:** any format — PRD, design doc, GitHub issue, free text, existing PROGRESS.md

**Output:**
- `tasks/01-<slug>.md` per task (with YAML frontmatter)
- `PROGRESS.yaml` with all tasks in `not_started`
- `ralph-config.yaml` scaffold (if missing)

**Three delivery mechanisms:**

| Mechanism | When | How |
|---|---|---|
| `ralph-loop init --from plan.md` | CLI, automated | Calls AI backend to parse plan and generate files |
| `docs/plan-to-tasks.md` | Interactive, any AI | User pastes prompt + plan into any chat, gets files to copy |
| `prompts/plan-to-tasks.prompt.md` | VS Code Copilot | `/plan-to-tasks` slash command if symlinked into `.github/prompts/` |

**The converter prompt instructs the AI to:**
- Read the source document
- Decompose into ordered phases and tasks
- For each task, generate a `.md` with: title, phase, description, acceptance criteria, files to touch, test plan, verify commands, reference implementation, constraints
- Add YAML frontmatter (phase, priority, verify_commands, visual_verify, files_to_touch, files_not_to_touch)
- Generate PROGRESS.yaml structure
- Output as structured JSON for parsing

**Context injected into the converter prompt:**
- Source plan (user provides)
- Project instructions (`AGENTS.md` / `CLAUDE.md` if present)
- Task format reference (embedded in prompt)
- PROGRESS.yaml schema (embedded in prompt)
- One complete example task file (embedded in prompt)

## Auth Forwarding (Docker mode)

| Service | Credential Source | Mount/Env |
|---|---|---|
| GitHub Copilot | `~/.config/gh/` | Volume mount |
| OpenAI Codex | `OPENAI_API_KEY` | Env var |
| Anthropic Claude | `~/.claude/` + `ANTHROPIC_API_KEY` | Volume mount + env var |

---

## Error Handling & Edge Cases

| Scenario | Behavior |
|---|---|
| Docker not installed | Bash script exits with error message + install instructions |
| Image build fails | Bash script exits with Docker build output |
| AI CLI times out | `timeout` command returns exit code 124; treated as code step failure |
| AI CLI returns non-zero | Treated as code step failure; stdout/stderr captured as feedback |
| Inspector output is not valid JSON | Attempt LLM normalization + Pydantic validation; if still invalid, treat entire stdout as feedback and verdict="fail" |
| PROGRESS.yaml parse error | Raise exception with file path + line number |
| Task .md file not found | Skip task, mark as abort with reason "task_file_not_found" |
| Config missing required field | Pydantic validation error with field name |
| All tasks aborted | `next-action` returns `{"command":"done","reason":"remaining_tasks_aborted"}` |
| Container can't write to workspace | Bash uses `-u $(id -u):$(id -g)` to match host user |
| Auth credential missing | CLI inside container fails; captured as code step failure with feedback |
| `.ralph-tmp/` exists from previous run | Cleared by bash script at startup (`rm -rf .ralph-tmp && mkdir .ralph-tmp`) |
| SIGINT (Ctrl+C) during loop | Bash trap cleans up `.ralph-tmp/`, exits with 130 |
| SIGTERM | Same as SIGINT |
| Task locked (IN_PROGRESS) at startup | If iteration-state.json exists, resume mid-iteration; otherwise reset to FAILED |
| Concurrent runs | Not supported; `.ralph-tmp/` acts as implicit lock. Second run detects existing dir and warns. |

---

## Implementation Phases (Detailed)

### Phase 1: Repository Scaffolding
**Files created:**
- `pyproject.toml` — as specified in § pyproject.toml Specifications
- `LICENSE` — MIT text
- `README.md` — minimal stub with package name and one-liner
- `.gitignore` — Python + Node + Docker + `.ralph-tmp/`
- `.github/workflows/ci.yml` — pytest + ruff + mypy + Docker build
- `ralph_loop/__init__.py` — `__version__ = "0.1.0"`
- `ralph_loop/__main__.py` — `from ralph_loop.cli import main; main()`

**Acceptance:** `pip install -e .` succeeds, `ralph-loop --help` prints usage.

### Phase 2: Core Data Models
**Files created:**
- `ralph_loop/config.py` — `RalphConfig`, `BackendConfig`, `AuthConfig`, `VisualVerifyConfig` as specified
- `ralph_loop/progress.py` — `Progress`, `TaskProgress`, `PhaseProgress`, `TaskStatus`, `PhaseStatus`, all state machine functions
- `ralph_loop/task.py` — `Task`, `TaskFrontmatter`, `.load()` parser
- `ralph_loop/feedback.py` — `FeedbackEntry`, `FeedbackSource`, `format_feedback_for_prompt()`, `truncate_output()`

**Tests:**
- `tests/test_config.py` — load valid config, missing required fields, default values, engine lookup
- `tests/test_progress.py` — YAML round-trip, `select_next_task` priority, state transitions (legal + illegal), `fail_task` with abort, `advance_phase`, atomic write
- `tests/test_task.py` — parse frontmatter, extract sections, missing sections, `get_verify_commands` fallback
- `tests/test_feedback.py` — format single attempt, format multiple attempts, truncation

**Acceptance:** All tests pass. Models can serialize/deserialize to/from YAML/JSON.

### Phase 3: Backend Abstraction
**Files created:**
- `ralph_loop/backends/__init__.py` — `BACKEND_REGISTRY`, `get_backend()`
- `ralph_loop/backends/base.py` — `Backend` Protocol, `ExecutionResult`
- `ralph_loop/backends/codex.py` — `CodexBackend`
- `ralph_loop/backends/copilot.py` — `CopilotBackend`
- `ralph_loop/backends/claude.py` — `ClaudeBackend`

**Tests:**
- `tests/test_backends.py` — mock `subprocess.run` for each backend, verify command construction, timeout handling, `is_available()` check

**Acceptance:** Each backend constructs the correct CLI command string. Registry resolves names to classes.

### Phase 4: Prompt Templates
**Files created:**
- `ralph_loop/prompts/coder.md.j2` — as specified in template skeleton
- `ralph_loop/prompts/inspector.md.j2` — as specified in template skeleton
- `ralph_loop/prompts/plan_to_tasks.md.j2` — plan conversion prompt

**Acceptance:** Templates render without error with sample data. Output is valid markdown.

### Phase 5: Verification Pipeline
**Files created:**
- `ralph_loop/verification/__init__.py`
- `ralph_loop/verification/deterministic.py` — run commands, capture results
- `ralph_loop/verification/ai_inspection.py` — build prompt, call backend, parse verdict
- `ralph_loop/verification/visual.py` — screenshot + compare (stub for v1 if Playwright not installed)
- `ralph_loop/verification/pipeline.py` — orchestrate all verifiers, aggregate `VerificationReport`

**Tests:**
- `tests/test_verification.py` — mock backends, test pipeline aggregation, test "run all even on failure" behavior

**Acceptance:** Pipeline runs all verifiers regardless of individual failures. Aggregated report is correct.

### Phase 6: CLI Commands
**Files created:**
- `ralph_loop/cli.py` — all Click commands as specified

**Tests:**
- `tests/test_cli.py` — Click `CliRunner` tests for: `status`, `validate`, `next-action` (with mocked progress), `list-engines`

**Acceptance:** All CLI commands work. `next-action` drives the state correctly.

### Phase 7: Host Bash Orchestrator
**Files created:**
- `ralph-loop` (root) — full bash script as specified

**Tests:**
- Manual: run `./ralph-loop run` with mocked containers (dummy Docker images that just write success results)

**Acceptance:** Full loop executes: `next-action → code → verify → inspect → update → next-action`. Handles `done`, `pause`, `abort`.

### Phase 8: Docker
**Files created:**
- `Dockerfile` — multi-stage as specified
- `docker/entrypoint.sh`
- `.dockerignore`

**Tests:**
- `docker build --target base` succeeds
- `docker build --target codex` succeeds
- `docker build --target copilot` succeeds
- `docker build --target claude` succeeds
- `docker run ralph-loop-base status --help` prints usage

**Acceptance:** All 4 images build. Base container can run CLI commands.

### Phase 9: Docker Sandbox Decorator
**Files created:**
- `ralph_loop/backends/sandbox.py` — `SandboxBackend` as specified

**Tests:**
- `tests/test_backends.py` — test SandboxBackend generates correct `docker run` command

**Acceptance:** `uvx ralph-loop run --sandbox docker` wraps each CLI call in appropriate container.

### Phase 10: Plan-to-Tasks Converter
**Files created:**
- `ralph_loop/prompts/plan_to_tasks.md.j2` (if not already in Phase 4)
- `docs/plan-to-tasks.md` — standalone prompt (copy-paste version)
- `prompts/plan-to-tasks.prompt.md` — VS Code Copilot slash command version

**Acceptance:** `ralph-loop init --from examples/minimal-2-tasks/plan.md` generates valid task files + PROGRESS.yaml.

### Phase 11: Skills
**Files created:**
- `ralph_loop/skills/run-preflight/SKILL.md`
- `ralph_loop/skills/visual-compare/SKILL.md`

**Acceptance:** SKILL.md files have valid YAML frontmatter with name/description.

### Phase 12: Examples
**Files created:**
- `examples/minimal-2-tasks/` — ralph-config.yaml, PROGRESS.yaml, tasks/*.md
- `examples/multi-phase-visual/` — 6-task 2-phase example with visual_verify

**Acceptance:** `ralph-loop validate --config examples/minimal-2-tasks/ralph-config.yaml` passes.

### Phase 13: Docs, CI, Release
**Files created:**
- `README.md` — full documentation (quickstart, config reference, architecture)
- `docs/configuration.md` — full config reference
- `docs/DESIGN.md` — this document
- `.github/workflows/publish.yml` — PyPI publish on tag
- `CHANGELOG.md`

**Acceptance:** README has quickstart that works. CI passes. `pip install ralph-loop` works from PyPI.

---

## Verification Plan (Detailed)

### Unit Tests (`tests/`)

| Test File | Covers | Key Scenarios |
|---|---|---|
| `test_config.py` | `config.py` | Valid YAML load, missing required fields → ValidationError, default values, `get_backend()` missing role → KeyError, `get_auth()` unknown engine → empty AuthConfig |
| `test_progress.py` | `progress.py` | YAML round-trip (load→save→load identical), `select_next_task` prefers FAILED over NOT_STARTED, `select_next_task` returns None when all completed, `lock_task` illegal transition → ValueError, `fail_task` increments retries, `fail_task` → ABORT when retries >= max, `complete_task` updates phase status, `advance_phase` when all tasks done, atomic write (tmp + rename) |
| `test_task.py` | `task.py` | Parse valid frontmatter, extract acceptance criteria from markdown, missing sections → defaults, `get_verify_commands` with and without task override |
| `test_feedback.py` | `feedback.py` | Format single attempt, format 3 attempts accumulated, `truncate_output` at boundary, empty feedback list → empty string |
| `test_backends.py` | `backends/*.py` | Each backend: correct `subprocess.run` args (mock), timeout wrapping, model flag injection, `is_available()` with/without binary. SandboxBackend: correct `docker run` command construction |
| `test_verification.py` | `verification/*.py` | Pipeline runs ALL verifiers even when first fails, aggregation logic, parse inspector JSON verdict, fallback on malformed JSON, deterministic verifier captures all command results |
| `test_cli.py` | `cli.py` | `CliRunner` tests: `status` with sample PROGRESS.yaml, `validate` with valid/invalid config, `list-engines` deduplication, `next-action` with mocked progress |

### Contract Tests (Critical Paths)

These tests validate protocol-level behavior that must remain stable across refactors.

| Contract | Test Cases | Expected Result |
|---|---|---|
| Trusted verify command execution (`bash -lc`) | Command with pipes (`cmd1 \| cmd2`), redirects (`>`, `2>&1`), chaining (`&&`), env assignment (`FOO=1 cmd`) | Command executes correctly, exit code is preserved, output is captured and truncated, parent shell state is unchanged |
| No `eval` side effects | Command containing shell metacharacters and variable references | Behavior matches isolated subshell semantics; no variable/function leakage to parent shell |
| Inspector strict JSON parse | Inspector returns exact valid JSON object | Parsed directly with `json.loads`, validated by Pydantic, verdict consumed without fallback |
| Inspector normalization fallback | Inspector returns non-JSON or mixed prose+JSON | Normalization LLM returns strict schema JSON, Pydantic validation passes, normalized verdict consumed |
| Inspector defensive fallback | Raw output cannot be normalized into valid schema | Verdict defaults to `fail`; full raw output is stored as feedback details |
| Retry feedback continuity | Two consecutive failed attempts with mixed verifier failures | Both attempts are appended in order; no previous feedback is overwritten |

### Integration Tests (`tests/test_integration.py`)

**Scenario:** 3-task PROGRESS.yaml, all mocked backends.
1. Task 01: coder succeeds, tests pass, inspector passes → COMPLETED
2. Task 02: coder succeeds, tests FAIL, inspector FAIL → FAILED with feedback → retry → COMPLETED
3. Task 03: coder fails 3 times → ABORT

**Assertions:**
- Final PROGRESS.yaml has tasks 01+02 as COMPLETED, task 03 as ABORT
- Task 02 has 1 feedback entry (from first failure)
- Task 03 has 3 feedback entries
- Phase status is correctly updated

### Docker Tests (CI)

- `docker build --target base` exits 0
- `docker build --target codex` exits 0
- `docker build --target copilot` exits 0
- `docker build --target claude` exits 0
- `docker run ralph-loop-base --help` prints usage

### CLI Smoke Tests

- `ralph-loop --help` exits 0
- `ralph-loop status --config examples/minimal-2-tasks/ralph-config.yaml` prints summary
- `ralph-loop validate --config examples/minimal-2-tasks/ralph-config.yaml` exits 0

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Build standalone (not fork Ralphy) | Our 4 differentiators are architecturally incompatible with Ralphy |
| Docker primary, uvx secondary | Zero Python dependency for consumers; Docker is the universal dev dependency |
| Per-CLI container images | Different container per CLI = true isolation, no Docker socket, no DinD |
| Tests run on HOST | User configures their own test command; ralph-loop captures output. Zero project dependency assumptions |
| Step-by-step loop (not full plan) | Simpler bash, brain maintains state between calls via PROGRESS.yaml + `.ralph-tmp/` |
| File-based communication | `.ralph-tmp/` directory instead of piping JSON through Docker. Simpler, debuggable, inspectable |
| Single Dockerfile with multi-stage | One file to maintain. Build targets produce 4 images |
| 3 backends from v1 | User wants Copilot + Codex + Claude Code. Backend Protocol makes adding more trivial |
| Git submodule integration | Allows pinning version and inspecting source alongside project code |
| Sandbox in v1 | Docker sandbox for AI CLI execution when running via uvx, requested from day one |
| Click for CLI | Lightweight, widely used, no heavy framework |
| Pydantic for config/progress | Validation, serialization, type safety |
| Jinja2 for prompts | Already proven in current ralph templates |
| MIT license | Maximum adoption for public project |
| Plan-to-tasks converter | Onboarding UX: transforms any plan into ralph-loop-ready task files + PROGRESS.yaml |
