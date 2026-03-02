---
phase: 1
priority: high
verify_commands:
  - "echo verify"
visual_verify: null
contract_file: null
files_to_touch:
  - "src/hello.py"
files_not_to_touch: []
---

# Task 01: Hello world module

## Description

Create a simple hello module.

## Acceptance Criteria

1. Module exists.
2. Function returns a greeting string.

## Test Plan

1. Run configured verification command.

## Constraints

- Keep implementation small.
