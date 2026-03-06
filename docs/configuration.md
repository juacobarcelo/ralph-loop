# Configuration Reference

## Scope

`ralph-loop` now treats config as **global defaults** (engines, auth, retries, default verification).

Runtime files are resolved from a loop directory (`--loop-dir`, default `.`):

- `progress_file`: `<loop_dir>/PROGRESS.yaml`
- `task_dir`: `<loop_dir>/tasks/`
- `pause_file`: `<loop_dir>/PAUSE.md`
- `workspace_dir`: `<loop_dir>/product/`

You can still provide any of these paths explicitly in YAML to override the convention.

## Required

- `backends`: Backend configuration for at least `coder`

## Optional

- `max_retries` (default `3`)
- `verify_commands` (default `[]`)
- `project_instructions` (optional explicit path; if omitted, auto-detected from loop dir ancestors: `AGENTS.md`, `CLAUDE.md`, `COPILOT.md`)
- `backends.init` or `backends.initialize` for init plan generation role (falls back to `inspector`, then `coder`)
- `auth` map (`codex`, `copilot`, `claude`) with:
  - `env`: list of env vars to forward in Docker mode
  - `mount`: list of host paths to mount inside backend containers
- `docker_run_args` for flow-specific Docker runtime flags:
  - `default`: applied to every `docker run`
  - `contexts`: applied by wrapper context (`base`, `init`, `code`, `inspect`, `review`, `visual`)
  - `engines`: applied by engine (`codex`, `copilot`, `claude`, `base`)
- `agent_capabilities` for task-scoped tool declarations:
  - keys are capability IDs referenced from task JSON (`agent_capabilities.code/review`)
  - `type`: `builtin` or `mcp`
  - `instruction`: short text injected into coder/reviewer prompts
  - `check_command` (optional): host command that must succeed before invoking the agent step
  - `backend_flags` (optional): engine-specific CLI flags appended per step (`code`, `review`)

## Config location

- CLI default: `$RALPH_CONFIG` when set, otherwise `~/.config/ralph-loop/config.yaml`
- Override per command with `--config`

## Example

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
  - "./bin/run-tests tests/"

auth:
  codex:
    env: [OPENAI_API_KEY]
    mount: []
  copilot:
    env: []
    mount: ["~/.config/gh"]

docker_run_args:
  default: ["--network", "bridge"]
  contexts:
    visual: ["--cpus=1.0"]
    review: ["--memory=2g"]
  engines:
    codex: ["--security-opt=no-new-privileges:true"]

agent_capabilities:
  chrome-devtools:
    type: mcp
    instruction: "Use Chrome MCP only when acceptance criteria require runtime/UI evidence."
    check_command: "command -v google-chrome"
    backend_flags:
      codex:
        code: ["--config", "mcp_servers.chrome-devtools=enabled"]
        review: ["--config", "mcp_servers.chrome-devtools=enabled"]
  playwright:
    type: builtin
    instruction: "Use Playwright scripts for deterministic UI checks when needed."
```
