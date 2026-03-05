from __future__ import annotations

from ralph_loop.feedback import filter_feedback_for_coder, format_feedback_for_prompt, truncate_output
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


def test_filter_feedback_for_coder_excludes_test_output() -> None:
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
                ),
                FeedbackSource(type="code", verdict="fail", details="Type error on line 8"),
                FeedbackSource(
                    type="visual",
                    verdict="fail",
                    details="Header misaligned",
                    output="raw-visual-output",
                ),
            ],
        )
    ]

    filtered = filter_feedback_for_coder(entries)
    assert len(filtered) == 1
    sources = filtered[0].sources
    assert len(sources) == 2
    assert all(source.type != "test" for source in sources)
    visual = next(source for source in sources if source.type == "visual")
    assert visual.output is None
