# /plan-to-tasks

Convert the following project plan into ralph-loop tasks.

Output strict JSON only with this shape:

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
