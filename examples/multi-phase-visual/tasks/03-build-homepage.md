---
phase: 2
priority: high
verify_commands:
  - "echo verify"
visual_verify:
  type: screenshot
  url: http://localhost:3000
  reference: references/home.png
  assertion: homepage layout matches reference
  viewport_width: 1280
  viewport_height: 720
contract_file: null
files_to_touch:
  - "web/homepage.tsx"
files_not_to_touch: []
---

# Task 03: Build homepage

## Description

Implement homepage UI for the project.

## Acceptance Criteria

1. Homepage renders expected layout.
2. Visual check can compare with reference.

## Test Plan

1. Run configured verification command.

## Constraints

- Keep layout accessible.
