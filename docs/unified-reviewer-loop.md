# Unified Reviewer Loop

## Status
- Proposed on 2026-03-06
- Target scope: replace the current `visual + inspect` pipeline with a single reviewer agent

## Summary
This design changes `ralph-loop` from a fixed multi-phase verification pipeline:

`code -> verify -> visual -> inspect -> update`

to a reviewer-driven orchestration model:

`runtime_pre_code -> code -> runtime_post_code -> review -> update`

Key properties:
- `ralph-loop` remains an orchestrator, not a reviewer.
- A dedicated reviewer agent decides how to review.
- Runtime/service health is owned by the orchestrator, not by the coder or reviewer.
- The reviewer receives a read-only workspace and cannot modify product code.
- Existing task files remain usable during migration.

## Problem Statement
The current architecture bakes review methodology into the orchestrator:
- deterministic commands run in `verify`
- screenshot review runs in `visual`
- diff review runs in `inspect`

This causes several structural problems:
- tasks that mix code, runtime behavior, and UI are forced through a rigid review path
- visual verification is treated as mandatory when many tasks do not require it
- the reviewer cannot choose to use browser/runtime only when acceptance criteria demand it
- infra failures and code failures are not clearly separated
- the orchestrator partially acts as a reviewer by deciding which review modalities apply

The desired model is two-agent:
- `coder`: changes code
- `reviewer`: decides whether the task was successfully completed

The orchestrator should only:
- prepare context
- enforce isolation
- ensure runtime readiness
- route outputs
- manage retries and progress state

## Goals
- Keep `ralph-loop` as an orchestration system, not an embedded reviewer.
- Introduce a single `review` step handled by a dedicated reviewer backend.
- Ensure the orchestrator verifies required services before code execution.
- Re-check runtime after coding so infra breakage caused by the coder is attributed immediately.
- Prevent the reviewer from modifying code by mounting the workspace read-only.
- Preserve compatibility with existing generated tasks during rollout.
- Require structured JSON output from the reviewer.

## Non-Goals
- Building a bespoke tool-calling framework inside `ralph-loop`
- Encoding service topology directly into Ralph task files
- Giving the reviewer write access to the repository
- Requiring MCP for all review tasks
- Solving per-task auto-commit in v1

## Proposed Flow

### 1. `runtime_pre_code`
The orchestrator runs a project-defined preflight command on the host before the coder is invoked.

Responsibilities:
- start required services
- verify readiness
- fail fast if the environment is unhealthy

Behavior:
- if `runtime_pre_code` fails, the coder is not invoked
- the task does not consume a coding attempt
- default policy should pause the loop or fail with an infra-specific reason

### 2. `code`
The coder runs as today with read/write access to the workspace.

Additional behavior:
- the orchestrator records `task_base_sha` before the coder runs
- the orchestrator stores runtime-preflight success in iteration state

### 3. `runtime_post_code`
The orchestrator re-runs the same preflight command after the coder completes.

Responsibilities:
- detect when the coder broke runtime startup or readiness
- attribute the failure before sending the task to the reviewer

Behavior:
- if `runtime_pre_code` passed and `runtime_post_code` fails, the attempt fails immediately
- the reviewer is not invoked
- the failure is attributed to the coder and appended as retry feedback

### 4. `review`
A dedicated reviewer agent decides how to inspect the work.

The reviewer receives:
- task metadata
- acceptance criteria
- touched file boundaries from the task contract
- `task_base_sha`
- git diff from `task_base_sha` to the current workspace state
- runtime context and any preflight results
- project instructions
- explicit instructions that it may use shell, browser automation, or MCP if available

The reviewer decides whether to:
- inspect the diff only
- read code
- run local checks
- open a browser and validate runtime behavior
- use MCP if available and stable

The orchestrator does not prescribe the review method.

### 5. `update`
The orchestrator parses the reviewer JSON and updates task state.

Pass:
- reviewer returns valid JSON with `verdict=pass`

Fail:
- reviewer returns valid JSON with `verdict=fail`
- reviewer output is invalid or missing
- the reviewer process exits non-zero

## Runtime Guard Contract

### Rationale
The loop implementation should not know which services the project needs.

Instead, the loop designer provides a script or command with exit-code semantics.

### Proposed Config
```yaml
runtime_guards:
  pre_code:
    command: "./.ralph-loop/guard.sh pre"
    timeout_seconds: 180
    on_failure: "pause_loop"

  post_code:
    command: "./.ralph-loop/guard.sh post"
    timeout_seconds: 180
    on_failure: "fail_attempt"
```

### Execution Rules
- commands are executed on the host, not inside AI-agent containers
- exit code `0` means runtime healthy
- any non-zero exit code means runtime unhealthy
- stdout/stderr are captured and attached as structured feedback

### Failure Policies
Supported v1 policies:
- `pause_loop`
- `abort_loop`
- `fail_attempt`

Recommended defaults:
- `pre_code.on_failure = pause_loop`
- `post_code.on_failure = fail_attempt`

### Recommended Script Semantics
The project-specific script should:
- start required services
- wait for readiness using active checks, not `sleep` alone
- exit `0` only when the required runtime is actually usable

Example:
```bash
#!/usr/bin/env bash
set -euo pipefail

docker compose up -d videntus-dev-server
curl -fsS http://localhost:3001 >/dev/null
```

The loop should treat the script as opaque and rely only on exit code plus captured output.

## Reviewer Contract

### Reviewer Inputs
The review prompt must include:
- task title and description
- acceptance criteria
- constraints
- files-to-touch boundaries
- previous retry feedback
- runtime guard results
- `task_base_sha`
- git diff from `task_base_sha`
- optional legacy visual context from the task

### Reviewer Capabilities
The reviewer may use:
- shell
- git
- local file reads
- browser/runtime access via `host.docker.internal`
- Playwright or project scripts if present in the container
- MCP if configured for the chosen backend
- only capabilities explicitly declared for the task (`agent_capabilities.review`) and defined in loop config (`agent_capabilities`)

The reviewer must not modify code.

### Reviewer Output
The reviewer must return strict JSON only:
```json
{
  "verdict": "pass",
  "methods_used": ["diff", "runtime", "browser"],
  "feedback": "Header badge visible and 402 dialog verified.",
  "findings": []
}
```

Required fields:
- `verdict`: `pass` or `fail`
- `methods_used`: non-empty array of short strings
- `feedback`: concise summary
- `findings`: array of strings, empty on success

If the reviewer cannot gather enough evidence to approve, it must return `fail`.

## Read-Only Review Execution

### Container Requirements
The reviewer container should run with:
- workspace mounted read-only
- writable artifact directory mounted separately
- host networking alias for runtime checks
- temporary writable filesystem for browser/runtime tooling

Recommended `docker run` shape:
```bash
docker run --rm \
  --read-only \
  --add-host=host.docker.internal:host-gateway \
  -u "$DOCKER_USER" \
  -v "$PWD:/workspace:ro" \
  -v "$PWD/.ralph-tmp/review:/ralph-tmp:rw" \
  --tmpfs /tmp \
  --tmpfs /home/ralph/.cache \
  ralph-loop-codex review ...
```

### Write Locations
Allowed writable locations in review:
- `/ralph-tmp`
- `/tmp`
- tool-specific cache tmpfs mounts

Disallowed:
- `/workspace`

## Diff Model

### `task_base_sha`
At task lock time, the orchestrator records:
- `task_base_sha = git rev-parse HEAD`

This becomes the review anchor for the entire attempt.

### Review Diff
The reviewer receives the diff between:
- `task_base_sha`
- current workspace state after coding

This works regardless of whether later versions of the loop introduce per-task commits.

### Commit Policy
v1 recommendation:
- do not auto-commit before review
- review the current working tree against `task_base_sha`

If a future auto-commit policy is enabled:
- commit only after a passing review
- still compute the review diff using `task_base_sha`

## Task Contract Changes

### v1 Compatibility Strategy
Do not require immediate regeneration of all task files.

Existing task contracts continue to work as follows:
- `coding.acceptance_criteria` remains the canonical acceptance list
- `inspect.acceptance_criteria` may override or supplement review context
- `visual` becomes optional runtime context for the reviewer
- `agent_capabilities` is optional; when present it scopes what tool-capabilities each agent step may use:
  - `agent_capabilities.code`
  - `agent_capabilities.review`

The reviewer prompt should receive legacy fields if present:
- `visual.url`
- `visual.assertion`
- `visual.reference`

### Future Task Schema
In a later migration, tasks may define:
```json
{
  "review": {
    "service_urls": ["http://host.docker.internal:3001"],
    "runtime_expectations": ["Header badge and 402 dialog render correctly."],
    "focus": ["Review runtime/UI behavior for header state and credit gating."],
    "acceptance_criteria": ["..."]
  }
}
```

This future schema is optional and not required for v1 rollout.

## Config Changes

### New Backend Role
Add `reviewer` backend role with fallback to `inspector` if omitted.

Example:
```yaml
backends:
  coder:
    engine: codex
    model: gpt-5.3-codex

  reviewer:
    engine: codex
    model: gpt-5.4-codex
```

Fallback resolution:
1. `backends.reviewer`
2. `backends.inspect`
3. `backends.inspector`

### New Runtime Guard Section
Add:
```yaml
runtime_guards:
  pre_code:
    command: "./.ralph-loop/guard.sh pre"
    timeout_seconds: 180
    on_failure: "pause_loop"
  post_code:
    command: "./.ralph-loop/guard.sh post"
    timeout_seconds: 180
    on_failure: "fail_attempt"
```

### Optional Rollout Switch
Add a temporary switch:
```yaml
review_mode: "unified_agent"
```

Accepted values:
- `legacy`
- `unified_agent`

This allows incremental rollout without breaking current loops.

## Iteration State Changes

### Current State
Today the iteration state is minimal:
```json
{
  "task_id": "06",
  "current_step": "code",
  "started_at": "2026-03-06T03:20:00Z",
  "results": []
}
```

### Proposed State
Add:
```json
{
  "task_id": "06",
  "current_step": "runtime_pre_code",
  "started_at": "2026-03-06T03:20:00Z",
  "task_base_sha": "abc123...",
  "runtime_pre_ok": false,
  "results": []
}
```

Fields:
- `task_base_sha`: git anchor for review
- `runtime_pre_ok`: whether the pre-code runtime guard succeeded

`current_step` values in unified mode:
- `runtime_pre_code`
- `code`
- `runtime_post_code`
- `review`
- `update`

## Step Result Schema

### Runtime Guard Result
```json
{
  "step": "runtime_pre_code",
  "task_id": "06",
  "exit_code": 0,
  "stdout": "...",
  "stderr": "..."
}
```

### Review Result
```json
{
  "step": "review",
  "task_id": "06",
  "exit_code": 0,
  "stdout": "{\"verdict\":\"pass\",\"methods_used\":[\"diff\"],\"feedback\":\"...\",\"findings\":[]}"
}
```

## Feedback Model Changes

### New Feedback Source Types
Add:
- `runtime_guard`
- `review`

### Coder Feedback Filtering
Feedback passed back to the coder should include:
- runtime guard failures
- reviewer findings

Reviewer successes should not be forwarded.

## State Machine Changes

### Unified Mode Transition Rules
1. New task selected
2. lock task
3. record `task_base_sha`
4. `runtime_pre_code`
5. if pass -> `code`
6. `runtime_post_code`
7. if pass -> `review`
8. `update`

### Failure Matrix

#### `runtime_pre_code` fails
- task remains unreviewed
- coder is not invoked
- retry count does not increment
- loop pauses or aborts per policy

#### `code` exits non-zero
- task attempt fails
- retry count increments
- coder feedback includes code failure only

#### `runtime_post_code` fails after `runtime_pre_code` passed
- attempt fails immediately
- retry count increments
- feedback source is `runtime_guard`
- reviewer is not invoked

#### `review` fails
- attempt fails
- retry count increments
- feedback source is `review`

#### reviewer output invalid
- attempt fails
- retry count increments
- feedback source is `review`
- details explain invalid JSON / missing fields

## Orchestrator Responsibilities
The orchestrator is responsible for:
- starting required services before coding
- verifying runtime health before coding
- re-verifying runtime health after coding
- capturing `task_base_sha`
- generating diff context
- enforcing reviewer read-only execution
- parsing structured reviewer output
- applying retries and progress transitions

The orchestrator is not responsible for:
- deciding which review method is appropriate
- performing visual review itself
- interpreting acceptance criteria beyond pass/fail contracts

## Implementation Plan

### Phase 1: Config and Data Model
Files:
- `ralph-loop/ralph_loop/config.py`
- `ralph-loop/ralph_loop/progress.py`
- `ralph-loop/ralph_loop/feedback.py`

Changes:
- add `runtime_guards`
- add `review_mode`
- add `reviewer` backend resolution
- add new feedback source types

### Phase 2: Orchestration State Machine
Files:
- `ralph-loop/ralph_loop/cli.py`

Changes:
- add `runtime_pre_code` and `runtime_post_code`
- add `task_base_sha` to iteration state
- replace `visual + inspect` transitions with `review`
- update `update` to consume review output

### Phase 3: Host Dispatcher
Files:
- `ralph-loop/ralph-loop`

Changes:
- add host-side runtime guard execution
- add reviewer execution path
- execute reviewer container with read-only workspace mount
- make host alias available to reviewer

### Phase 4: Reviewer Prompt and Parsing
Files:
- new `ralph-loop/ralph_loop/prompts/reviewer.md.j2`
- new `ralph-loop/ralph_loop/review.py`

Changes:
- build reviewer prompt from task, diff, runtime context, and legacy visual hints
- parse strict reviewer JSON
- normalize malformed output to fail

### Phase 5: Test Coverage
Files:
- `ralph-loop/tests/test_cli.py`
- new review-specific tests if needed

Tests to add:
- pre-code runtime guard failure pauses loop without invoking coder
- post-code runtime guard failure fails attempt and skips reviewer
- unified review path emits `review` next-action
- reviewer fallback backend resolution works
- reviewer invalid JSON fails safely
- read-only review execution command includes host alias and writable temp mounts

## Rollout Strategy

### Step 1
Implement `review_mode: unified_agent` behind a config flag.

### Step 2
Test against:
- a minimal sample loop
- an existing loop with legacy `visual` fields

### Step 3
Make unified mode default for new loops.

### Step 4
Update `init` to emit `review` metadata instead of `visual + inspect`.

Status (implemented in this repo):
- `init` now auto-ensures unified-mode loop defaults in config:
  - `review_mode: unified_agent`
  - `backends.reviewer`
  - `runtime_guards.pre_code` and `runtime_guards.post_code`
- `init` also auto-creates/validates `.ralph-loop/guard.sh` and `.ralph-loop/verify-commands.txt` in the configured workspace.

### Step 5
Deprecate but temporarily keep legacy `visual` and `inspect` parsing for backward compatibility.

## Open Decisions
- Whether to keep a separate optional `evidence` stage for cheap automatic checks
- Whether successful tasks should be auto-committed by the orchestrator in a future version
- Whether the reviewer should run in the same Docker network as project services or always use `host.docker.internal`

## Recommendation
For v1 of this redesign:
- remove mandatory `verify`
- rely on `runtime_pre_code` and `runtime_post_code` for infra health
- use a single reviewer agent with a read-only workspace
- preserve legacy task compatibility
- ship behind `review_mode: unified_agent`
