from __future__ import annotations

from ralph_loop.progress import FeedbackEntry


def filter_feedback_for_coder(feedback_entries: list[FeedbackEntry]) -> list[FeedbackEntry]:
    """Return feedback sources relevant for the coding step."""
    filtered: list[FeedbackEntry] = []
    for entry in feedback_entries:
        sources = []
        for source in entry.sources:
            if source.type in {"code", "ai_inspection", "review", "runtime_guard"}:
                sources.append(source.model_copy(deep=True))
                continue
            if source.type == "visual":
                visual_source = source.model_copy(deep=True)
                visual_source.command = None
                visual_source.exit_code = None
                visual_source.output = None
                sources.append(visual_source)
        if sources:
            filtered.append(
                FeedbackEntry(
                    attempt=entry.attempt,
                    timestamp=entry.timestamp,
                    sources=sources,
                )
            )
    return filtered


def truncate_output(output: str, max_chars: int = 4000) -> str:
    """Return the tail of output limited to max_chars."""
    if len(output) <= max_chars:
        return output
    return output[-max_chars:]


def format_feedback_for_prompt(feedback_entries: list[FeedbackEntry]) -> str:
    """Format accumulated feedback entries as markdown for coder prompts."""
    if not feedback_entries:
        return ""

    lines: list[str] = ["## Previous Attempt Feedback", ""]
    for entry in feedback_entries:
        lines.append(f"### Attempt {entry.attempt} ({entry.timestamp})")
        lines.append("")

        for source in entry.sources:
            if source.type == "test":
                lines.extend(
                    [
                        "**Test result:**",
                        f"- Verdict: {source.verdict.upper()}",
                        f"- Command: `{source.command or 'n/a'}`",
                        f"- Exit code: {source.exit_code if source.exit_code is not None else 'n/a'}",
                    ]
                )
                if source.output:
                    lines.append("- Output:")
                    lines.append("```")
                    lines.append(truncate_output(source.output))
                    lines.append("```")
            else:
                lines.extend(
                    [
                        f"**{source.type.replace('_', ' ').title()}:**",
                        f"- Verdict: {source.verdict.upper()}",
                        f"- Details: {source.details or 'n/a'}",
                    ]
                )
            lines.append("")

    return "\n".join(lines).strip() + "\n"
