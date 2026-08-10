"""Real macOS backend end-to-end test (SPEC.md 23, 27) - not mocked.

Skipped outside macOS; Linux/Windows backends are unit-tested against
mocked subprocess.run elsewhere since chattr/icacls can't be exercised on
this dev machine.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from chwrite import cli
from chwrite.backends import query_path
from chwrite.backends.macos import protect_macos, unprotect_macos
from chwrite.state import load_state, save_state

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="exercises the real macOS chflags backend"
)


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "protected.txt").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)


def test_acceptance_apply_verify_unlock_apply(tmp_path) -> None:
    """SPEC.md section 23's acceptance test, run literally."""
    _init_repo(tmp_path)
    target = tmp_path / "protected.txt"

    result = protect_macos(str(target), hard=False)
    assert result["level"] == "ENFORCED"

    with pytest.raises(PermissionError):
        target.write_text("changed")

    level, _backend = query_path(str(target))
    assert level == "ENFORCED"

    unprotect_macos(str(target), {"backend": "macos-uchg", "original_mode": 0o644})
    target.write_text("changed")
    assert target.read_text() == "changed"

    result = protect_macos(str(target), hard=False)
    assert result["level"] == "ENFORCED"
    with pytest.raises(PermissionError):
        target.write_text("changed again")

    unprotect_macos(str(target), {"backend": "macos-uchg", "original_mode": 0o644})


def test_apply_ten_times_is_idempotent(tmp_path) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".chwrite").write_text("version 1\n\nprotect protected.txt\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "policy"], cwd=tmp_path, check=True)

    root = str(tmp_path)
    reports = []
    for _ in range(10):
        state = load_state(root)
        _, report = cli.reconcile(root, state)
        save_state(root, state)
        reports.append(report)

    assert len(reports[0]) == 1
    assert all(r == [] for r in reports[1:])

    state = load_state(root)
    unprotect_macos(str(tmp_path / "protected.txt"), state["files"]["protected.txt"])
