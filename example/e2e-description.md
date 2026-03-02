# Ralph Loop E2E Example (Copilot)

This example is intended to validate the full Ralph cycle from plan to execution.

The cycle should create a small Python product inside `tmp/product/`:
- package name: `mini_calc`
- operations: `add`, `subtract`, `multiply`
- CLI usage: `python -m mini_calc add 2 3` should print `5`
- tests under `tmp/product/tests/`
- a tiny web page under `tmp/product/web/index.html` for visual verification

Scope constraints:
- only create or edit files under `tmp/product/`
- keep implementation small and readable
- use standard library only

Implementation checklist:
- [ ] Create `tmp/product/mini_calc/core.py` with typed operation functions.
- [ ] Create `tmp/product/mini_calc/__init__.py` exporting public functions.
- [ ] Create `tmp/product/mini_calc/cli.py` with basic command parsing.
- [ ] Create `tmp/product/tests/test_core.py`.
- [ ] Create `tmp/product/tests/test_cli.py`.
- [ ] Create `tmp/product/web/index.html` with a visible heading "Mini Calc".
- [ ] Ensure `python -m pytest -q ./tmp/product/tests` passes.

Visual verification requirement:
- Add one task with `visual_verify` configured to check `tmp/product/web/index.html`.
- Use a data URL as target (no local server required).
- Do not use any reference image; the LLM must inspect the current screenshot directly.
- Assertion: the page contains a clear "Mini Calc" title and a short subtitle.
