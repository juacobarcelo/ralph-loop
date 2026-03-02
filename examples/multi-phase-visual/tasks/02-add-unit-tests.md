---
phase: 1
priority: medium
verify_commands:
  - "echo verify"
visual_verify: null
contract_file: null
files_to_touch:
  - "tests/test_service.py"
files_not_to_touch: []
---

# Task 02: Add unit tests

## Description

Add unit tests for service module.

## Acceptance Criteria

1. Tests cover main service behavior.
2. Error cases are checked.

## Test Plan

1. Run configured verification command.

## Constraints

- Keep tests deterministic.
