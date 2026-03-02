---
phase: 1
priority: high
verify_commands:
  - "echo verify"
visual_verify: null
contract_file: null
files_to_touch:
  - "src/service.py"
files_not_to_touch: []
---

# Task 01: Create service module

## Description

Add a service module with core methods.

## Acceptance Criteria

1. Service module is created.
2. Public method signatures are stable.

## Test Plan

1. Run configured verification command.

## Constraints

- Keep API small.
