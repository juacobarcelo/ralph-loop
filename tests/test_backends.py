from __future__ import annotations

import subprocess
from types import SimpleNamespace

from ralph_loop.backends import get_backend
from ralph_loop.backends.sandbox import SandboxBackend
from ralph_loop.config import AuthConfig


def test_get_backend_known() -> None:
    backend = get_backend("codex")
    assert backend.name == "codex"


def test_get_backend_unknown() -> None:
    try:
        _ = get_backend("unknown")
        assert False
    except KeyError:
        assert True


def test_codex_execute(monkeypatch) -> None:
    backend = get_backend("codex")

    captured = {}

    def fake_run(cmd, capture_output, text, cwd, check):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = backend.execute("hello", model="gpt")
    assert result.exit_code == 0
    assert captured["cmd"][0] == "timeout"


def test_copilot_execute_uses_prompt_flag(monkeypatch) -> None:
    backend = get_backend("copilot")
    captured = {}

    def fake_run(cmd, capture_output, text, cwd, check):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    result = backend.execute(
        "hello world",
        model="gpt-5",
        timeout_seconds=10,
        extra_flags=["--silent"],
    )

    assert result.exit_code == 0
    assert captured["cmd"][0] == "timeout"
    assert "--prompt" in captured["cmd"]
    assert "hello world" in captured["cmd"]
    assert "--model" in captured["cmd"]
    assert "gpt-5" in captured["cmd"]


def test_sandbox_backend_builds_docker_command(monkeypatch, tmp_path) -> None:
    inner = get_backend("codex")
    auth = AuthConfig(
        env=["OPENAI_API_KEY"],
        mount=[
            {
                "source": str(tmp_path / "auth"),
                "target": str(tmp_path / "auth"),
            }
        ],
    )
    backend = SandboxBackend(inner=inner, auth_config=auth, workspace_dir=str(tmp_path))

    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hello", encoding="utf-8")

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()

    captured = {}

    def fake_run(cmd, capture_output, text, check, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setattr("subprocess.run", fake_run)

    result = backend.execute(
        prompt=str(prompt_file),
        model="gpt-5",
        timeout_seconds=123,
        extra_flags=["--json"],
        cwd=str(tmp_path),
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert captured["cmd"][0:3] == ["docker", "run", "--rm"]
    assert f"{tmp_path}:/workspace" in captured["cmd"]
    assert "ralph-loop-codex" in captured["cmd"]
    assert "execute" in captured["cmd"]
    assert "OPENAI_API_KEY=secret" in captured["cmd"]
    assert f"{auth_dir}:{auth_dir}" in captured["cmd"]
    assert "/workspace/prompt.md" in captured["cmd"]
    assert "--model" in captured["cmd"]
    assert "gpt-5" in captured["cmd"]
    assert "--timeout-seconds" in captured["cmd"]
    assert "123" in captured["cmd"]
    assert "--extra-flag" in captured["cmd"]
    assert "--json" in captured["cmd"]


def test_sandbox_backend_uses_explicit_auth_mount_target(monkeypatch, tmp_path) -> None:
    inner = get_backend("codex")
    auth = AuthConfig(
        mount=[
            {
                "source": str(tmp_path / "auth"),
                "target": "~/.codex",
            }
        ]
    )
    backend = SandboxBackend(inner=inner, auth_config=auth, workspace_dir=str(tmp_path))

    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("hello", encoding="utf-8")

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()

    captured = {}

    def fake_run(cmd, capture_output, text, check, timeout):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = backend.execute(prompt=str(prompt_file), timeout_seconds=30, cwd=str(tmp_path))

    assert result.exit_code == 0
    assert f"{auth_dir}:/home/ralph/.codex" in captured["cmd"]


def test_sandbox_backend_writes_prompt_and_handles_timeout(monkeypatch, tmp_path) -> None:
    inner = get_backend("copilot")
    backend = SandboxBackend(inner=inner, auth_config=AuthConfig(), workspace_dir=str(tmp_path))

    def fake_run(cmd, capture_output, text, check, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout, output="out", stderr="err")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = backend.execute(prompt="inline prompt", timeout_seconds=1)

    assert result.exit_code == 124
    assert result.timed_out is True
    assert result.stdout == "out"
    assert result.stderr == "err"
    prompt_files = list((tmp_path / ".ralph-tmp").glob("sandbox-prompt-*.md"))
    assert len(prompt_files) == 1
    assert prompt_files[0].read_text(encoding="utf-8") == "inline prompt"
