# Configuration Reference

## Scope

`ralph-loop` now treats config as **global defaults** (engines, auth, retries, optional deterministic verification).

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
- `review_mode` (`legacy` or `unified_agent`; default `legacy`)
- `verify_commands` (default `[]`)
- `runtime_guards` for orchestrator-owned pre/post coding checks:
  - `pre_code`: lightweight service availability for the coder; in unified loops this should fail fast if required services are down
  - `post_code`: lightweight runtime health check after coding; if services are broken, prefer diagnostics over broad repo-wide verification
- `project_instructions` (optional explicit path; if omitted, auto-detected from loop dir ancestors: `AGENTS.md`, `CLAUDE.md`, `COPILOT.md`)
- `backends.init` or `backends.initialize` for init plan generation role (falls back to `inspector`, then `coder`)
- `auth` map (`codex`, `copilot`, `claude`) with:
  - `env`: list of env vars to forward in Docker mode
  - `mount`: list of objects with `source` and `target`
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
review_mode: unified_agent

backends:
  init:
    engine: copilot
    model: claude-opus-4-6
    timeout_seconds: 300
  coder:
    engine: codex
    model: gpt-5.3-codex
    timeout_seconds: 600
  reviewer:
    engine: codex
    model: gpt-5.3-codex
    timeout_seconds: 300

verify_commands: []

runtime_guards:
  pre_code:
    command: "./.ralph-loop/guard.sh pre"
    timeout_seconds: 60
    on_failure: abort_loop
  post_code:
    command: "./.ralph-loop/guard.sh post"
    timeout_seconds: 60
    on_failure: fail_attempt

auth:
  codex:
    env: [OPENAI_API_KEY]
    mount: []
  copilot:
    env: []
    mount:
      - source: ~/.config/gh
        target: ~/.config/gh

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

For `review_mode=unified_agent`, prefer this contract:

- keep top-level `verify_commands: []`
- keep task-level `verify_commands` empty by default
- use `runtime_guards.pre_code` for coder-facing service availability
- use `runtime_guards.post_code` for post-code runtime health and diagnostics
- reserve heavier repo-wide typecheck/test suites for a separate acceptance step outside the loop

## Auth mount format

`auth.<engine>.mount` is a list of bind-mount declarations:

```yaml
auth:
  codex:
    env: []
    mount:
      - source: ~/.codex/auth.json
        target: ~/.codex/auth.json
```

- `source`: host file or directory that contains the backend credentials or session files
- `target`: destination path inside the container
- `source` and `target` both support `~`
- If `target` starts with `~/`, ralph-loop expands it against the home directory of the user running inside the container
- Before mounting, ralph-loop copies the source into a temporary staging path under `.ralph-tmp/`, so container writes do not modify the original host auth material directly
- If the source is a file, ralph-loop mounts the staged parent directory so the backend can keep ephemeral state alongside the credential file
