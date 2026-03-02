from __future__ import annotations

from ralph_loop.feedback import format_feedback_for_prompt, truncate_output
from ralph_loop.progress import FeedbackEntry, FeedbackSource


def test_truncate_output_tail() -> None:
    text = "a" * 5000
    truncated = truncate_output(text, max_chars=4000)
    assert len(truncated) == 4000
    assert truncated == text[-4000:]


def test_format_feedback_for_prompt() -> None:
    entries = [
        FeedbackEntry(
            attempt=1,
            timestamp="2026-03-01T14:32:00Z",
            sources=[
                FeedbackSource(
                    type="test",
                    verdict="fail",
                    command="pytest tests/test_x.py",
                    exit_code=1,
                    output="AssertionError",
                )
            ],
        )
    ]
    payload = format_feedback_for_prompt(entries)
    assert "Attempt 1" in payload
    assert "pytest tests/test_x.py" in payload
