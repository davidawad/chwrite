"""check-path exit-code tests (SPEC.md 17, 25)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CHWRITE_PY = Path(__file__).resolve().parent.parent / "chwrite.py"


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / ".chwrite").write_text(
        'version 1\n\nprotect protected.txt message="do not touch"\n'
    )
    (tmp_path / "protected.txt").write_text("x")
    (tmp_path / "normal.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)


def test_check_path_exit_codes(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    protected = subprocess.run(
        [sys.executable, str(CHWRITE_PY), "check-path", "protected.txt"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert protected.returncode == 1
    assert b"do not touch" in protected.stderr

    unprotected = subprocess.run(
        [sys.executable, str(CHWRITE_PY), "check-path", "normal.txt"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert unprotected.returncode == 0
    assert unprotected.stderr == b""


def test_verify_exit_codes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    subprocess.run([sys.executable, str(CHWRITE_PY), "apply", "--quiet"], cwd=tmp_path, check=True)

    clean = subprocess.run(
        [sys.executable, str(CHWRITE_PY), "verify", "--quiet"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert clean.returncode == 0

    subprocess.run(
        [sys.executable, str(CHWRITE_PY), "unlock", "protected.txt"], cwd=tmp_path, check=True
    )
    (tmp_path / "protected.txt").write_text("tampered")

    # Simulate the file having drifted out of protection without going
    # through `chwrite unlock` (e.g. some other process bypassing OS
    # enforcement): mark it locked again in state without re-applying
    # OS-level protection, which is what verify is meant to catch.
    git_dir = subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout.strip()
    state_file = tmp_path / git_dir / "chwrite" / "state.json"
    state = json.loads(state_file.read_text())
    state["files"]["protected.txt"]["locked"] = True
    state_file.write_text(json.dumps(state))

    dirty = subprocess.run(
        [sys.executable, str(CHWRITE_PY), "verify", "--quiet"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )
    assert dirty.returncode == 1
    assert b"protected.txt" in dirty.stderr
