# Configuration Reference

## Required

- `progress_file`: Path to `PROGRESS.yaml`
- `task_dir`: Directory containing task markdown files
- `backends`: Backend configuration for at least `coder`

## Optional

- `max_retries` (default `3`)
- `pause_file` (default `PAUSE.md`)
- `workspace_dir` (default current workspace)
- `verify_commands` (default `[]`)
- `project_instructions` (optional path to `AGENTS.md`/`CLAUDE.md`)
- `auth` map (`codex`, `copilot`, `claude`) with:
  - `env`: list of env vars to forward in Docker mode
  - `mount`: list of host paths to mount read-only

## Example

```yaml
progress_file: PROGRESS.yaml
task_dir: tasks/
max_retries: 3
pause_file: PAUSE.md
workspace_dir: .

backends:
  coder:
    engine: codex
    model: gpt-5.3-codex
    timeout_seconds: 600
  inspector:
    engine: copilot
    model: claude-opus-4-6
    timeout_seconds: 300

verify_commands:
  - "./bin/run-tests tests/"

auth:
  codex:
    env: [OPENAI_API_KEY]
    mount: []
  copilot:
    env: []
    mount: ["~/.config/gh"]
```
