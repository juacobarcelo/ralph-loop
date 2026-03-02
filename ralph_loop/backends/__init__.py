from __future__ import annotations

from ralph_loop.backends.base import Backend
from ralph_loop.backends.claude import ClaudeBackend
from ralph_loop.backends.codex import CodexBackend
from ralph_loop.backends.copilot import CopilotBackend
from ralph_loop.backends.sandbox import SandboxBackend

BACKEND_REGISTRY: dict[str, type[Backend]] = {
    "codex": CodexBackend,
    "copilot": CopilotBackend,
    "claude": ClaudeBackend,
}


def get_backend(engine: str, **kwargs: object) -> Backend:
    """Instantiate backend by engine name."""
    if engine not in BACKEND_REGISTRY:
        raise KeyError(f"Unknown backend engine: {engine}")
    backend_class = BACKEND_REGISTRY[engine]
    return backend_class(**kwargs)


__all__ = ["BACKEND_REGISTRY", "SandboxBackend", "get_backend"]
