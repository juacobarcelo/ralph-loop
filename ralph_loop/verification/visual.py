from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Template
from pydantic import BaseModel, ValidationError

from ralph_loop.backends.base import Backend
from ralph_loop.config import VisualVerifyConfig
from ralph_loop.task import Task


class VisualVerdict(BaseModel):
    """Normalized schema for visual verification verdicts."""

    verdict: str
    feedback: str


@dataclass
class VisualVerificationResult:
    """Result of visual verification."""

    verdict: str
    details: str
    raw_output: str = ""


def run_visual_verification(
    *,
    config: VisualVerifyConfig | None,
    workspace_dir: str,
    backend: Backend,
    model: str | None,
    timeout_seconds: int,
    extra_flags: list[str],
    task: Task,
) -> VisualVerificationResult:
    """Run visual verification through screenshot capture and LLM review."""
    if config is None:
        return VisualVerificationResult(verdict="pass", details="visual verification not configured")

    workspace = Path(workspace_dir).resolve()
    reference_path: Path | None = None
    if config.reference:
        reference_path = _resolve_reference_path(config.reference, workspace)
        if not reference_path.exists():
            return VisualVerificationResult(
                verdict="fail",
                details=f"visual reference image not found: {reference_path}",
            )

    screenshot_result = _capture_screenshot(config, workspace)
    if screenshot_result.error is not None:
        return VisualVerificationResult(verdict="fail", details=screenshot_result.error)

    screenshot_path = screenshot_result.path
    if screenshot_path is None:
        return VisualVerificationResult(verdict="fail", details="failed to capture screenshot")

    prompt = _render_visual_prompt(
        task=task,
        config=config,
        workspace=workspace,
        screenshot_path=screenshot_path,
        reference_path=reference_path,
    )
    response = backend.execute(
        prompt=prompt,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags,
        cwd=str(workspace),
    )
    raw_output = response.stdout.strip()
    parsed = _parse_or_normalize_visual_output(
        raw_output=raw_output,
        backend=backend,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags,
        cwd=str(workspace),
    )

    if response.exit_code != 0:
        details = parsed.feedback or response.stderr.strip()
        if not details:
            details = f"visual backend exited with code {response.exit_code}"
        return VisualVerificationResult(verdict="fail", details=details, raw_output=raw_output)

    return VisualVerificationResult(
        verdict=parsed.verdict,
        details=parsed.feedback,
        raw_output=raw_output,
    )


@dataclass
class _ScreenshotResult:
    path: Path | None = None
    error: str | None = None


def _capture_screenshot(config: VisualVerifyConfig, workspace: Path) -> _ScreenshotResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _ScreenshotResult(
            error="visual verification requested but playwright is not installed",
        )

    output_dir = workspace / ".ralph-tmp" / "visual"
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = output_dir / f"current-{uuid.uuid4().hex}.jpg"

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(
                viewport={
                    "width": config.viewport_width,
                    "height": config.viewport_height,
                }
            )
            target_url = _normalize_visual_target_url(config.url, workspace)
            page.goto(target_url, wait_until="networkidle")
            page.screenshot(
                path=str(screenshot_path),
                full_page=True,
                type="jpeg",
                quality=50,
            )
            browser.close()
    except Exception as error:  # noqa: BLE001
        return _ScreenshotResult(error=f"visual screenshot capture failed: {error}")

    return _ScreenshotResult(path=screenshot_path)


def _normalize_visual_target_url(raw_url: str, workspace: Path) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme in {"http", "https", "data"}:
        return raw_url

    if parsed.scheme == "file":
        path_value = _extract_file_url_path(parsed)
        return _resolve_local_path(path_value, workspace).as_uri()

    if parsed.scheme:
        return raw_url

    return _resolve_local_path(raw_url, workspace).as_uri()


def _extract_file_url_path(parsed: object) -> str:
    # Keep helper narrow and local for predictable file:// normalization.
    file_parsed = parsed
    netloc = str(getattr(file_parsed, "netloc", "")).strip()
    path = str(getattr(file_parsed, "path", "")).strip()

    if netloc and netloc != "localhost":
        return f"{netloc}{path}"
    return path


def _resolve_local_path(path_value: str, workspace: Path) -> Path:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    for base in (workspace, *workspace.parents):
        resolved = (base / candidate).resolve()
        if resolved.exists():
            return resolved

    return (workspace / candidate).resolve()


def _resolve_reference_path(reference: str, workspace: Path) -> Path:
    return _resolve_local_path(reference, workspace)


def _render_visual_prompt(
    *,
    task: Task,
    config: VisualVerifyConfig,
    workspace: Path,
    screenshot_path: Path,
    reference_path: Path | None,
) -> str:
    template_path = Path(__file__).resolve().parents[1] / "prompts" / "visual.md.j2"
    template = Template(template_path.read_text(encoding="utf-8"))

    return template.render(
        task=task,
        visual_config=config,
        workspace_dir=str(workspace),
        screenshot_path=_path_for_prompt(screenshot_path, workspace),
        reference_path=(
            _path_for_prompt(reference_path, workspace) if reference_path is not None else None
        ),
    )


def _path_for_prompt(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def _parse_or_normalize_visual_output(
    *,
    raw_output: str,
    backend: Backend,
    model: str | None,
    timeout_seconds: int,
    extra_flags: list[str],
    cwd: str,
) -> VisualVerdict:
    direct = _try_parse_verdict(raw_output)
    if direct is not None:
        return direct

    normalization_prompt = (
        "Normalize the following visual verification output into strict JSON with schema "
        '{"verdict":"pass|fail","feedback":"string"}. '
        "Return JSON only. If uncertain, set verdict to fail.\n\n"
        f"RAW OUTPUT:\n{raw_output}"
    )
    normalized = backend.execute(
        prompt=normalization_prompt,
        model=model,
        timeout_seconds=timeout_seconds,
        extra_flags=extra_flags,
        cwd=cwd,
    )
    normalized_parsed = _try_parse_verdict(normalized.stdout)
    if normalized_parsed is not None:
        return normalized_parsed

    fallback_feedback = raw_output.strip() or "visual verifier output could not be normalized"
    return VisualVerdict(verdict="fail", feedback=fallback_feedback)


def _try_parse_verdict(payload: str) -> VisualVerdict | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    try:
        parsed = VisualVerdict.model_validate(data)
    except ValidationError:
        return None

    verdict = parsed.verdict.strip().lower()
    if verdict not in {"pass", "fail"}:
        return None

    return VisualVerdict(verdict=verdict, feedback=parsed.feedback.strip())
