# Copilot E2E Demo Product

## Objective

Build a tiny Python product in `tmp/product/` to validate the full ralph-loop cycle end to end.

## Product Scope

- A package named `mini_calc`.
- Core operations: `add`, `subtract`, and `multiply`.
- A CLI entry point to run operations from the terminal.
- Unit tests for operations and one CLI integration test.

## Constraints

- Create and modify files only under `tmp/product/`.
- Keep code Python 3.11+ compatible.
- Use only standard library modules.
- Keep implementation small and readable.

## Acceptance Criteria

- `tmp/product/mini_calc/__init__.py` exports `add`, `subtract`, and `multiply`.
- `tmp/product/mini_calc/core.py` implements the operations with type hints.
- `tmp/product/mini_calc/cli.py` supports command pattern:
  `python -m mini_calc add 2 3` and prints `5`.
- `tmp/product/tests/test_core.py` validates all operations.
- `tmp/product/tests/test_cli.py` validates one CLI path.
- `python -m pytest -q ./tmp/product/tests` passes.
