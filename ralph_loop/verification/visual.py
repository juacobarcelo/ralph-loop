from __future__ import annotations

from dataclasses import dataclass

from ralph_loop.config import VisualVerifyConfig


@dataclass
class VisualVerificationResult:
    """Result of visual verification."""

    verdict: str
    details: str


def run_visual_verification(
    config: VisualVerifyConfig | None, workspace_dir: str
) -> VisualVerificationResult:
    """Run visual verification or return pass when not configured."""
    del workspace_dir
    if config is None:
        return VisualVerificationResult(
            verdict="pass", details="visual verification not configured"
        )

    try:
        import playwright  # type: ignore  # noqa: F401
    except ImportError:
        return VisualVerificationResult(
            verdict="fail",
            details="visual verification requested but playwright is not installed",
        )

    return VisualVerificationResult(
        verdict="fail",
        details="visual verification implementation is not available yet",
    )
