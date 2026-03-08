# Agent Instructions — ralph-loop

## Project Snapshot

- **What**: `ralph-loop` is a pip-installable / Docker-primary Python package that orchestrates AFK AI coding loops with multi-model-per-phase strategy, feedback accumulation across retries, and hybrid verification (deterministic + AI inspection + visual).
- **Status**: Greenfield.
- **Python**: ≥ 3.11 (target 3.12 for Docker images).
- **License**: MIT.
- **Primary distribution**: Docker (bash script builds/runs containers).
- **Secondary distribution**: `uvx ralph-loop run` (Python package via PyPI).
- **Build system**: Hatchling (`pyproject.toml`).
- **Frameworks / key dependencies**: Click (CLI), Pydantic ≥ 2.0 (models), PyYAML (config/progress), Jinja2 (prompt templates), python-frontmatter (task files).
- **Optional dependencies**: Playwright + Pillow (visual verification).
- **Dev dependencies**: pytest, pytest-cov, ruff, mypy (strict mode).

## Directory Structure

```
ralph-loop/
├── ralph-loop                     # [BASH] Host entry point — Docker-primary CLI
├── Dockerfile                     # Multi-stage: base → codex, copilot, claude
├── pyproject.toml                 # Package config (hatchling)
├── ralph_loop/                    # Python package root
│   ├── __init__.py                # __version__
│   ├── __main__.py                # python -m ralph_loop
│   ├── cli.py                     # Click CLI (all subcommands)
│   ├── loop.py                    # Native loop orchestrator (uvx path)
│   ├── config.py                  # RalphConfig Pydantic model
│   ├── progress.py                # Progress state machine (PROGRESS.yaml)
│   ├── task.py                    # Task model + .md frontmatter parser
│   ├── feedback.py                # FeedbackEntry model + prompt formatter
│   ├── backends/                  # AI CLI backend abstraction
│   │   ├── base.py                # Backend Protocol + ExecutionResult
│   │   ├── codex.py               # CodexBackend
│   │   ├── copilot.py             # CopilotBackend
│   │   ├── claude.py              # ClaudeBackend
│   │   └── sandbox.py             # SandboxBackend (Docker wrapper)
│   ├── verification/              # Verification pipeline
│   │   ├── deterministic.py       # Run verify_commands, collect ALL results
│   │   ├── ai_inspection.py       # Inspection prompt + backend call
│   │   ├── visual.py              # Screenshot capture + AI comparison
│   │   └── pipeline.py            # Orchestrate verifiers, aggregate report
│   ├── prompts/                   # Jinja2 prompt templates
│   │   ├── coder.md.j2
│   │   ├── inspector.md.j2
│   │   └── plan_to_tasks.md.j2
│   └── skills/                    # Agent skills (agentskills.io)
├── docker/
│   └── entrypoint.sh
├── tests/                         # pytest test suite
├── examples/                      # Example configs + task files
├── docs/                          # Documentation
└── prompts/                       # VS Code Copilot slash command prompts
```

## Development Environment

- **Host Python**: 3.10 (used only for IDE support — autocomplete, navigation).
- **Runtime Python**: 3.12 inside Docker (all tests, lint, type checks, and CI run here).
- Python 3.12 features (`X | Y`, `match/case`, `StrEnum`) will not parse on the host interpreter — this is expected.

### Host Bootstrap (IDE support only)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

This venv enables IDE features (Pylance, mypy daemon, ruff). It is NOT used for running tests or validating code.

### Build Dev Image

```bash
docker build --target dev -t ralph-loop-dev .
```

The `dev` stage extends `base` with dev dependencies (pytest, pytest-cov, ruff, mypy). It uses `/workspace` as working directory and mounts the host repo.

### Validate Changes (always run before committing)

All validation runs inside the dev container against Python 3.12:

```bash
# Full validation suite (lint + type check + tests)
docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev sh -c "\
  ruff check ralph_loop/ tests/ && \
  ruff format --check ralph_loop/ tests/ && \
  mypy ralph_loop/ && \
  python -B -m pytest tests/ -v --cov=ralph_loop --cov-report=term-missing"

# Individual commands (for faster iteration)
docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev \
  python -B -m pytest tests/ -v --cov=ralph_loop --cov-report=term-missing

docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev \
  ruff check ralph_loop/ tests/

docker run --rm -v "$PWD:/workspace" -w /workspace ralph-loop-dev \
  mypy ralph_loop/
```

### Build Production Images

```bash
docker build --target base    -t ralph-loop-base    .
docker build --target codex   -t ralph-loop-codex   .
docker build --target copilot -t ralph-loop-copilot .
docker build --target claude  -t ralph-loop-claude  .
```

## Coding Conventions

- Python ≥ 3.11 features are encouraged: `X | Y` union syntax, `match/case`, `StrEnum`.
- Use `from __future__ import annotations` in every module.
- Type annotations on all public functions and methods; `mypy --strict` must pass.
- Pydantic `BaseModel` for all data structures that cross module boundaries (config, progress, task, feedback).
- Use `@runtime_checkable` `Protocol` for backend abstraction — not ABC.
- Line length: 100 characters (configured in `ruff`).
- String quotes: double quotes.
- Imports: `ruff` isort-compatible ordering (stdlib → third-party → local).
- Docstrings: Google style, mandatory on all public classes and functions.
- No `print()` calls in library code; use structured returns or logging. `print()` is acceptable only in `cli.py` for user-facing output.
- Prefer `pathlib.Path` over `os.path` for filesystem operations.
- Atomic file writes: write to `.tmp` then `os.rename()`.
- Error handling: raise typed exceptions with descriptive messages; let Click handle user-facing display.

## Architecture Guardrails

1. **`./docs/design.md` is the single source of truth** — do not deviate from its data models, state machines, or CLI signatures without updating the spec first.
2. **Backend Protocol is the only abstraction for AI CLIs** — all backends implement the `Backend` Protocol from `backends/base.py`. No ad-hoc subprocess calls.
3. **State lives in PROGRESS.yaml** — the progress state machine (`progress.py`) is the sole owner of task state transitions.
4. **Feedback accumulation is append-only** — never discard or overwrite previous feedback entries. Each failed attempt appends a `FeedbackEntry` with all verification sources.
5. **Run ALL verifiers on every attempt** — deterministic, visual, and AI inspection all run regardless of individual failures. This maximizes feedback per retry.
6. **Prompt templates are Jinja2 only** — all prompts live in `ralph_loop/prompts/*.j2`. No inline prompt construction in Python code.
7. **File-based communication for Docker mode** — the `.ralph-tmp/` directory is the sole communication channel between bash orchestrator and containers. No pipes, no Docker socket.
8. **Bash script is dumb** — the `ralph-loop` bash script dispatches commands; all logic lives in the Python package (`ralph-loop-base` container).
9. **Containers are isolated and disposable** — each container gets only the auth credentials it needs. No Docker socket mount.
10. **Configuration uses Pydantic validation** — `RalphConfig` validates all fields at load time. Invalid config must fail fast with clear error messages.

## Testing

- All tests run inside the `ralph-loop-dev` Docker image (Python 3.12). Never run pytest on the host (Python 3.10).
- Tests live in `tests/` and follow `test_<module>.py` naming.
- Use `pytest` fixtures in `conftest.py` for shared setup (temp progress files, mock backends, sample configs).
- Mock `subprocess.run` for backend tests — never call real AI CLIs in unit tests.
- Test state transitions exhaustively: legal transitions succeed, illegal transitions raise `ValueError`.
- Integration test (`test_integration.py`) runs a full loop with mocked backends on a 3-task PROGRESS.yaml covering: pass, retry-then-pass, and abort scenarios.
- Test coverage target: ≥ 90% on `ralph_loop/`.
- Click CLI tests use `click.testing.CliRunner`.

## Language Policy

All agent output must be in English: code, comments, variable names, docstrings, commit messages, test names, and documentation.

## Workflow

- Group related changes into logical commits with conventional commit messages (e.g., `feat: add progress state machine`, `test: add config validation tests`).
- Do not commit after every small change — commit when a coherent unit of work is complete.
- In `review_mode=unified_agent`, keep runtime guards lightweight: `pre_code` for service availability, `post_code` for runtime health/diagnostics, and `verify_commands` empty by default unless a task-local deterministic check is explicitly required.
- Always run lint + type check + tests before committing.
- When implementing a phase, create all files for that phase before moving to the next.
- When a decision requires clarification, ask — do not guess.

## Done Criteria (Code Tasks)

- Any code-writing task is incomplete until quality gates pass, even if no PR is created.
- Validate in `ralph-loop-dev` container (Python 3.12), not on host Python.
- Required gates: `ruff check ralph_loop/ tests/`, `ruff format --check ralph_loop/ tests/`, `mypy ralph_loop/`, and `python -B -m pytest tests/ -v`.
- If formatting fails, run `ruff format` on affected files and re-run checks.
- Keep fixes minimal and targeted to the reported failure; avoid over-engineered or unrelated refactors.
- Optional dependencies (e.g., `playwright`) must not break baseline typing or CI when optional extras are not installed.
