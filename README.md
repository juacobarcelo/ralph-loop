# ralph-loop

**AFK AI coding loop orchestrator** with multi-model-per-phase strategy, feedback accumulation across retries, and hybrid verification (deterministic + AI inspection + visual).

Ships with 3 backends — **Codex**, **GitHub Copilot**, and **Claude Code** — and runs Docker-first for full isolation or natively via `uvx`.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python ≥ 3.11](https://img.shields.io/badge/Python-≥3.11-blue.svg)](https://python.org)

---

## Root Config Workflow (Init → Run)

If your `ralph-config.yaml` is in the repository root, and your plan is under `PRD/...`, run from repo root like this:

```bash
# 1) Initialize tasks/progress from a plan
./ralph-loop init --from PRD/extractor_frames_desde_video/plan.md

# 2) Execute loop against the generated loop directory
./ralph-loop run PRD/extractor_frames_desde_video/.ralph-loop/<plan-slug>
```

If `PRD/.../.ralph-loop/` contains exactly one generated plan, you can pass the parent directory and `ralph-loop` auto-selects it.

Equivalent explicit form:

```bash
CONFIG=ralph-config.yaml LOOP_DIR=PRD/extractor_frames_desde_video/.ralph-loop/<plan-slug> ./ralph-loop run
```

Use the same `LOOP_DIR` for status/validation:

```bash
./ralph-loop status PRD/extractor_frames_desde_video/.ralph-loop
./ralph-loop validate PRD/extractor_frames_desde_video/.ralph-loop
./ralph-loop check PRD/extractor_frames_desde_video/.ralph-loop
```

---

## What It Does

ralph-loop takes a list of coding tasks (markdown files with YAML frontmatter) and iterates through them autonomously:

1. **Select** the next task from `PROGRESS.yaml` (retry-first policy).
2. **Code** — send the task + accumulated feedback to a coder backend (Codex, Copilot, or Claude).
3. **Verify** — run _all_ verifiers on every attempt (deterministic commands, AI diff inspection, visual screenshot comparison).
4. **Feedback** — on failure, append structured feedback and retry (up to `max_retries`).
5. **Advance** — on success, mark the task complete and move to the next.

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   Select Task ────▶  Code (AI)    ────▶  Verify (ALL)  ────▶  Pass / Fail  
└─────────────┘     └──────────────┘     └──────────────┘     └──────┬───────┘
       ▲                                                             │
       └─────────────────── retry with feedback ◄────────────────────┘
```

### Features

- **Multi-model per task phase** — use different AI backends for coding vs. inspection (e.g., Codex codes, Copilot reviews).
- **Feedback accumulation across retries** — every failed attempt appends structured feedback to the next prompt, so the AI learns from previous mistakes.
- **AI-powered diff inspection** — an inspector backend reviews the git diff against acceptance criteria and returns a pass/fail verdict.
- **Visual screenshot verification** — optional Playwright-based screenshot capture reviewed by AI, with or without a reference image.
- **Run ALL verifiers every attempt** — deterministic tests, AI inspection, and visual checks all run regardless of individual failures, maximizing signal per retry.
- **Docker-isolated per-backend containers** — each AI CLI runs in its own container with only the credentials it needs. No Docker socket mount.
- **Plan-to-tasks generation** — convert a markdown plan into structured task files and a PROGRESS.yaml tracker, using AI generation.
- **Pause/resume** — drop a `PAUSE.md` file to pause the loop; remove it to resume.
- **Retry-first scheduling** — failed tasks are retried before new tasks are started.
- **Phase-based progression** — tasks are grouped into phases; the loop advances to the next phase when all tasks in the current phase are done or aborted.
- **Atomic state persistence** — PROGRESS.yaml is written atomically (tmp + rename) to prevent corruption.
- **Three execution modes** — Docker-primary, native via `uvx`, or native with Docker sandbox per AI call.

---

## Quick Start

### Option A: Docker (recommended)

Build the images:

```bash
./ralph-loop build-images
```

Or manually:

```bash
docker build --target base    -t ralph-loop-base    .
docker build --target codex   -t ralph-loop-codex   .
docker build --target copilot -t ralph-loop-copilot .
docker build --target claude  -t ralph-loop-claude  .
```

Run the loop:

```bash
./ralph-loop run
```

Check progress:

```bash
./ralph-loop status
```

### Option B: Native via `uvx`

```bash
# Run directly (AI CLIs must be installed on host)
uvx ralph-loop run

# Run with Docker sandbox for AI CLIs
uvx ralph-loop run --sandbox docker
```

### Execution Modes

| Mode | Command | AI CLIs Run In | Isolation |
|---|---|---|---|
| **Docker primary** | `./ralph-loop run` | Per-CLI containers | Full |
| **Native** | `uvx ralph-loop run` | Host (subprocess) | None |
| **Native + sandbox** | `uvx ralph-loop run --sandbox docker` | Per-CLI containers | Per-call |

---

## Getting Started

### 1. Create a project plan

Write a markdown file describing what you want to build:

```markdown
# My Project Plan

- [ ] Create the data models
- [ ] Implement the API endpoints
- [ ] Add authentication middleware
- [ ] Write integration tests
```

### 2. Generate tasks from the plan

```bash
./ralph-loop init --from plan.md
```

You can steer task generation with additional directives:

```bash
./ralph-loop init \
  --from plan.md \
  --instructions "Review only at the end visually that all buttons are visible and legible"
```

Or provide a directives file:

```bash
./ralph-loop init --from plan.md --instructions-file generation-directives.md
```

Both flags are composable. When both are present, `--instructions-file` content is appended first,
then `--instructions`.

`init` now asks the backend to write an intermediate `generated-plan.json` under `.ralph-tmp/`,
validates it with `ralph-loop validate-plan`, and only then materializes `tasks/*.json` and
`PROGRESS.yaml`.

This creates a dedicated loop directory next to the plan:

- `.ralph-loop/<plan-slug>/tasks/`
- `.ralph-loop/<plan-slug>/PROGRESS.yaml`
- `.ralph-loop/<plan-slug>/product/`

It uses an AI backend to decompose your plan into structured, actionable tasks.

### How to influence `init` generation (LLM steering contract)

`init` builds a single plan-to-tasks prompt from three sources (lowest to highest priority):

1. `project_instructions` (auto-discovered `AGENTS.md` / `CLAUDE.md` / `COPILOT.md`, or explicit config)
2. Source plan (`--from`)
3. User directives (`--instructions-file`, then `--instructions`)

`init` also resolves its backend role with this precedence:

1. `backends.init`
2. `backends.initialize`
3. `backends.inspector`
4. `backends.coder`

You can still override per command with `--backend` and `--model`.

Use directives to control generated task metadata, not only prose. The generator should emit tasks with
frontmatter-compatible fields that drive the loop:

- `acceptance_criteria`: concrete, testable checks.
- `verify_commands`: deterministic, host-side, non-destructive checks that confirm environment health and code-level regressions.
- `review`: runtime/service/browser review context (`focus`, `service_urls`, `runtime_expectations`) for the reviewer step.
- `files_to_touch` / `files_not_to_touch`: implementation boundaries.
- `constraints`: hard limits or guardrails for coding.

Directive examples that reliably shape output:

- `Review visually that the video list loads correctly.`
  - Expected effect: attach reviewer runtime context to the task that implements/renders the video list.
- `Review only at the end visually that all buttons are displayed and legible.`
  - Expected effect: attach reviewer runtime context only to the final relevant task.
- `Use verify_commands: python -m pytest -q tests and ruff check src`.
  - Expected effect: include deterministic host-side checks in generated tasks.

Tip for best results: keep plans focused on deliverables, and place strict behavioral constraints in
directives so they override inferred defaults.

### 3. Configure backends

Create or edit global config (`$RALPH_CONFIG` or `~/.config/ralph-loop/config.yaml`):

```yaml
max_retries: 3

backends:
  init:
    engine: copilot
    model: claude-opus-4-6
    timeout_seconds: 300
  coder:
    engine: codex
    model: gpt-5.3-codex
    timeout_seconds: 600
  inspector:
    engine: copilot
    model: claude-opus-4-6
    timeout_seconds: 300

verify_commands:
  - "python -m pytest tests/ -v"
  - "ruff check src/"

auth:
  codex:
    env: [OPENAI_API_KEY]
    mount: []
  copilot:
    env: []
    mount:
      - source: ~/.config/gh
        target: ~/.config/gh
```

### 4. Run the loop

```bash
CONFIG=~/.config/ralph-loop/config.yaml LOOP_DIR=.ralph-loop/my-project-plan ./ralph-loop run
```

ralph-loop will work through each task, coding → verifying → retrying until all tasks are complete or aborted.

### 5. Monitor progress

```bash
CONFIG=~/.config/ralph-loop/config.yaml LOOP_DIR=.ralph-loop/my-project-plan ./ralph-loop status
```

```
ralph-loop status
─────────────────────────────────
Phase 1: Bootstrap (IN PROGRESS)
  ✅ 01 - Create data models
  🔄 02 - Implement API endpoints
  ⬜ 03 - Add authentication middleware

Phase 2: Testing (NOT STARTED)
  ⬜ 04 - Write integration tests
─────────────────────────────────
Progress: 1/4 completed, 1 in progress, 2 not started
```

---

## E2E Example (Copilot + loop directory)

Use the committed example design in `example/` and let `init` create the loop directory automatically.

### Prerequisites

- Build required images:

```bash
docker build --target base -t ralph-loop-base .
docker build --target copilot -t ralph-loop-copilot .
```

- Ensure GitHub CLI auth is available for Copilot:

```bash
gh auth status
```

### 1. Generate tasks and progress

```bash
./ralph-loop init \
  --from example/copilot-e2e-plan.md \
  --config example/ralph-config.copilot.yaml
```

This creates:

- `./example/.ralph-loop/copilot-e2e-plan/PROGRESS.yaml`
- `./example/.ralph-loop/copilot-e2e-plan/tasks/*.md`
- `./example/.ralph-loop/copilot-e2e-plan/product/`

### 2. Validate and run the cycle

```bash
CONFIG=example/ralph-config.copilot.yaml LOOP_DIR=example/.ralph-loop/copilot-e2e-plan ./ralph-loop validate
CONFIG=example/ralph-config.copilot.yaml LOOP_DIR=example/.ralph-loop/copilot-e2e-plan ./ralph-loop run
CONFIG=example/ralph-config.copilot.yaml LOOP_DIR=example/.ralph-loop/copilot-e2e-plan ./ralph-loop status
```

The generated product and tests are written under `./example/.ralph-loop/copilot-e2e-plan/product/`, and deterministic verification runs with:

```bash
python -m pytest -q tests
```

---

## Architecture

```
Host (bash script: ./ralph-loop)
│
│  ┌─────────────────────────────────┐
├──│ ralph-loop-base                 │  "What's the next task?"
│  │ Python + ralph_loop package     │  → Returns JSON action
│  └─────────────────────────────────┘
│
│  ┌─────────────────────────────────┐
├──│ ralph-loop-codex                │  "Execute this coding prompt"
│  │ base + Codex CLI                │  → Writes code to mounted workspace
│  └─────────────────────────────────┘
│
│  bash -lc "verify_command"           Trusted commands run on HOST
│
│  ┌─────────────────────────────────┐
├──│ ralph-loop-copilot              │  "Inspect this diff"
│  │ base + Copilot CLI              │  → Returns verdict + feedback
│  └─────────────────────────────────┘
│
│  ┌─────────────────────────────────┐
└──│ ralph-loop-base                 │  "Update progress"
   │ Python                          │  → Writes PROGRESS.yaml
   └─────────────────────────────────┘
```

**Design properties:**
- **No Docker socket mount** — true isolation per container.
- **Each container is disposable** — no state persists inside containers.
- **Bash script is a dumb dispatcher** — all logic lives in Python inside `ralph-loop-base`.
- **Deterministic tests run on host** — user-configured commands via `bash -lc`.
- **Communication via files** — `.ralph-tmp/` directory, no pipes or sockets.

---

## Authentication

Each backend authenticates independently. ralph-loop does **not** manage credentials itself — it forwards them to the underlying CLI tools.

### Codex (OpenAI)

Codex supports two authentication methods:

| Method | How | Docker Config |
|---|---|---|
| **API key** (default) | Set `OPENAI_API_KEY` environment variable | Forward via `auth.codex.env` |
| **Authenticated account** | Run `codex auth` on the host to log in interactively | Mount config dir via `auth.codex.mount` |

**API key** — the simplest approach. Export the key and ralph-loop forwards it into containers:

```bash
export OPENAI_API_KEY="sk-..."
```

```yaml
auth:
  codex:
    env: [OPENAI_API_KEY]
    mount: []
```

**Already authenticated account** — if you've previously run `codex auth` and have an active session, you can mount the config directory instead of (or in addition to) an API key:

```yaml
auth:
  codex:
    env: []
    mount:
      - source: ~/.codex
        target: ~/.codex
```

Both methods can be combined. When both are present, the Codex CLI uses the API key first. In **native mode** (`uvx ralph-loop run` without `--sandbox docker`), Codex inherits the host environment directly, so any method that works on your host works automatically.

### GitHub Copilot

Copilot authenticates through the GitHub CLI (`gh`). You must be logged in via `gh auth login` on the host before running ralph-loop.

```yaml
auth:
  copilot:
    env: []
    mount:
      - source: ~/.config/gh
        target: ~/.config/gh
```

The `~/.config/gh` directory contains your GitHub session tokens and is mounted into Docker containers so `gh`/Copilot can reuse your existing host login. No environment variables are needed.

> **Tip:** Verify your session with `gh auth status` before starting a loop.

### Claude Code (Anthropic)

Claude Code supports two authentication methods:

| Method | How | Docker Config |
|---|---|---|
| **API key** | Set `ANTHROPIC_API_KEY` environment variable | Forward via `auth.claude.env` |
| **Authenticated account** | Run `claude auth` to log in interactively | Mount config dir via `auth.claude.mount` |

**API key:**

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

```yaml
auth:
  claude:
    env: [ANTHROPIC_API_KEY]
    mount: []
```

**Authenticated account:**

```yaml
auth:
  claude:
    env: []
    mount:
      - source: ~/.claude
        target: ~/.claude
```

### How Auth Forwarding Works

The `auth` section in `ralph-config.yaml` controls what gets passed into Docker containers:

- **`env`** — environment variables forwarded with `-e`. If the variable is set on the host, it's injected into the container. If not set, it's silently skipped.
- **`mount`** — list of objects with:
  - `source`: host directory to stage and mount into the container
  - `target`: destination path inside the container
- `source` and `target` both support `~` expansion.
- If `target` starts with `~/`, ralph-loop resolves it against the home directory of the user running inside the container.
- Auth mounts are staged through a temporary copy under `.ralph-tmp/` before `docker run`, so backend tools never write back into the original host auth directory directly.

```yaml
auth:
  codex:
    env: [OPENAI_API_KEY]
    mount:
      - source: ~/.codex
        target: ~/.codex
  copilot:
    env: []
    mount:
      - source: ~/.config/gh
        target: ~/.config/gh
  claude:
    env: [ANTHROPIC_API_KEY]
    mount:
      - source: ~/.claude
        target: ~/.claude
```

Example: `target: ~/.codex` becomes something like `/home/ralph/.codex` inside the container, depending on the container user configured by the runtime.

In **native mode** (no Docker), the `auth` section is ignored — backends inherit the full host environment and filesystem, so all authentication methods that work on the host work automatically.

### Quick Reference

| Backend | Env Variable | Config Directory | Needs Interactive Login? |
|---|---|---|---|
| **Codex** | `OPENAI_API_KEY` | `~/.codex` | Only for account auth (`codex auth`) |
| **Copilot** | — | `~/.config/gh` | Yes (`gh auth login`) |
| **Claude** | `ANTHROPIC_API_KEY` | `~/.claude` | Only for account auth (`claude auth`) |

---

## Task File Format

Tasks are markdown files with YAML frontmatter:

```markdown
---
phase: 1
priority: high
verify_commands:
  - "python -m pytest tests/test_api.py -v"
contract_file: null
files_to_touch:
  - "src/api.py"
files_not_to_touch:
  - "src/core.py"
---

# Task 01: Implement API endpoints

## Description

Create REST API endpoints for the user service.

## Acceptance Criteria

1. GET /users returns a list of users
2. POST /users creates a new user with validation
3. All endpoints return proper error responses

## Test Plan

1. Run `pytest tests/test_api.py`

## Constraints

- Follow existing code style
- Do not modify the database schema
```

---

## Verification Pipeline

Every attempt runs **all three** verification stages — even if earlier stages fail. This maximizes feedback per retry.

| Stage | How It Works | When It Runs |
|---|---|---|
| **Deterministic** | Runs `verify_commands` via `bash -lc`, collects exit codes + output | Always |
| **AI Inspection** | Sends git diff + criteria to inspector backend, expects `{"verdict":"pass\|fail","feedback":"..."}` | Always |
| **Reviewer Runtime Checks** | Reviewer may use browser/runtime tooling against `review.service_urls` when acceptance criteria require it | When task `review` context warrants it |

---

## CLI Commands

| Command | Description |
|---|---|
| `run` | Run the full orchestration loop |
| `status` | Print progress summary |
| `init --from PATH` | Generate tasks + progress from a plan document |
| `validate` | Check consistency between config, progress, and task files |
| `reset TASK_ID` | Reset a task to `not_started` status |
| `execute --prompt-file PATH` | Execute a coding prompt using an available AI backend |
| `inspect --prompt-file PATH` | Execute an inspection prompt |
| `next-action` | Determine next orchestration step (used by bash dispatcher) |
| `update` | Aggregate step results and update progress |
| `list-engines` | Print configured backend engines |

---

## Project Structure

```
ralph-loop/
├── ralph-loop                     # Bash entry point (Docker dispatcher)
├── Dockerfile                     # Multi-stage: base → dev, codex, copilot, claude
├── pyproject.toml                 # Package config (hatchling)
├── ralph_loop/                    # Python package
│   ├── cli.py                     # Click CLI (all subcommands)
│   ├── loop.py                    # Native loop orchestrator
│   ├── config.py                  # RalphConfig (Pydantic)
│   ├── progress.py                # Progress state machine (PROGRESS.yaml)
│   ├── task.py                    # Task model + frontmatter parser
│   ├── feedback.py                # Feedback model + prompt formatter
│   ├── backends/                  # AI CLI abstraction (codex, copilot, claude, sandbox)
│   ├── verification/              # Deterministic + AI inspection + visual pipeline
│   └── prompts/                   # Jinja2 templates (coder, inspector, plan_to_tasks)
├── tests/                         # pytest suite (31 tests)
├── examples/                      # Example configs + task files
└── docs/                          # Design spec + configuration reference
```

---

## Development

All validation runs inside Docker against Python 3.12:

```bash
# Build dev image
docker build --target dev -t ralph-loop-dev .

# Full validation suite
docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev sh -c "\
  ruff check ralph_loop/ tests/ && \
  ruff format --check ralph_loop/ tests/ && \
  mypy ralph_loop/ && \
  python -B -m pytest tests/ -v --cov=ralph_loop --cov-report=term-missing"
```

Individual commands for faster iteration:

```bash
# Tests only
docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev \
  python -B -m pytest tests/ -v

# Lint only
docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev \
  ruff check ralph_loop/ tests/

# Type check only
docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev \
  mypy ralph_loop/
```

### Host IDE Setup (optional)

For IDE support (Pylance, autocomplete) on the host:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

> Note: The host may run Python 3.10 — Python 3.12 features (`X | Y`, `match/case`) won't parse locally. This is expected; all validation runs in Docker.

---

## Configuration Reference

See [docs/configuration.md](docs/configuration.md) for the full config reference.

## Design Specification

See [docs/design.md](docs/design.md) for the complete architecture and data model specification.

---

## License

[MIT](LICENSE)
