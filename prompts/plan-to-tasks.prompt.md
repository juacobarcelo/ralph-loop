# /plan-to-tasks

Convert the following project plan into an intermediate `generated-plan.json` for ralph-loop.

Write the plan JSON to the output file path provided in the prompt, validate it with the provided command, and do not print the JSON to stdout. The file must use this shape:

```json
{
  "title": "string",
  "phases": [
    {
      "id": 1,
      "name": "string",
      "tasks": [
        {
          "id": "01",
          "title": "string",
          "description": "string",
          "acceptance_criteria": ["string"],
          "test_plan": "string",
          "priority": "high|medium|low",
          "verify_commands": ["string"],
          "review": {
            "focus": ["string"],
            "service_urls": ["string"],
            "runtime_expectations": ["string"]
          },
          "files_to_touch": ["string"],
          "files_not_to_touch": ["string"],
          "constraints": ["string"],
          "reference_impl": null
        }
      ]
    }
  ]
}
```

Plan:

{{input}}
