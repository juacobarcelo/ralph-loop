# Plan to Tasks Prompt

Use this prompt with any capable coding LLM. Paste your plan under "Source plan".

## Prompt

Convert the source plan into ralph-loop task files and PROGRESS structure.

Return STRICT JSON only with this schema:

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

Rules:
- Acceptance criteria must be specific and testable.
- Keep tasks implementation-sized.
- Use empty arrays where information is unknown.
- Do not output markdown fences outside the JSON response.

## Source plan

(Paste here)
