from __future__ import annotations

import os
import subprocess
from pathlib import Path


WRAPPER_PATH = Path(__file__).resolve().parents[1] / "ralph-loop"


def _run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["bash", str(WRAPPER_PATH), *args],
        cwd=cwd,
        env=merged_env,
        check=False,
        capture_output=True,
        text=True,
    )


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "tests@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralph Tests"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_run_preflight_fails_when_worktree_is_dirty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    loop_dir = repo / "loop"
    loop_dir.mkdir()
    (loop_dir / "PROGRESS.yaml").write_text("meta: {}\nphases: []\n", encoding="utf-8")
    (loop_dir / "ralph-config.yaml").write_text("workspace_dir: .\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )

    (repo / "tracked.txt").write_text("v2\n", encoding="utf-8")
    result = _run(
        ["run", str(loop_dir)],
        cwd=repo,
        env={"CONFIG": str(loop_dir / "ralph-config.yaml")},
    )

    assert result.returncode != 0
    combined_output = f"{result.stdout}\n{result.stderr}"
    assert "Preflight failed: worktree must be clean before 'ralph-loop run'." in combined_output
    assert "tracked.txt" in combined_output


def test_run_preflight_requires_loop_local_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)

    loop_dir = repo / "loop"
    loop_dir.mkdir()
    (loop_dir / "PROGRESS.yaml").write_text("meta: {}\nphases: []\n", encoding="utf-8")
    external_config = repo / "external-config.yaml"
    external_config.write_text("workspace_dir: .\n", encoding="utf-8")

    result = _run(
        ["run", str(loop_dir)],
        cwd=repo,
        env={"CONFIG": str(external_config)},
    )

    assert result.returncode != 0
    combined_output = f"{result.stdout}\n{result.stderr}"
    assert "Preflight failed: missing loop-local config file" in combined_output
