"""macOS backend branch tests against mocked chflags (SPEC.md 10, 27).

Complements tests/backends/test_macos.py's real end-to-end chflags test
(SPEC.md 23) with the fallback/edge branches that require *not* having a
working chflags on this box - chflags absent, chflags failing, hard=True's
informational note, and query_macos's non-flag branches.
"""

from __future__ import annotations

import stat
from unittest.mock import MagicMock, patch

from chwrite.backends.macos import protect_macos, query_macos, unprotect_macos


@patch("chwrite.backends.macos.shutil.which", return_value=None)
def test_protect_macos_falls_back_to_chmod_when_chflags_absent(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")

    result = protect_macos(str(target), hard=False)

    assert result == {"backend": "macos-chmod", "level": "READONLY", "hard": False}
    assert stat.S_IMODE(target.stat().st_mode) & 0o222 == 0


@patch("chwrite.backends.macos.shutil.which", return_value="/usr/bin/chflags")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_falls_back_to_chmod_when_chflags_fails(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1)

    result = protect_macos(str(target), hard=False)

    assert result["backend"] == "macos-chmod"
    assert result["level"] == "READONLY"


@patch("chwrite.backends.macos.shutil.which", return_value="/usr/bin/chflags")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_hard_prints_note_and_still_applies_enforced(
    mock_run, _which, tmp_path, capsys
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_macos(str(target), hard=True)

    assert result["level"] == "ENFORCED"
    assert result["hard"] is False
    assert "not implemented on macOS" in capsys.readouterr().err
    mock_run.assert_called_once_with(
        ["chflags", "uchg", str(target)], capture_output=True, check=False
    )


@patch("chwrite.backends.macos.shutil.which", return_value="/usr/bin/chflags")
@patch("chwrite.backends.macos.subprocess.run")
def test_unprotect_macos_calls_nouchg_before_restoring_mode(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    unprotect_macos(str(target), {"backend": "macos-uchg", "original_mode": 0o600})

    mock_run.assert_called_once_with(
        ["chflags", "nouchg", str(target)], capture_output=True, check=False
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_unprotect_macos_chmod_backend_skips_chflags(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)

    unprotect_macos(str(target), {"backend": "macos-chmod", "original_mode": 0o644})

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_unprotect_macos_without_original_mode_leaves_mode_alone(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)

    unprotect_macos(str(target), {"backend": "macos-chmod", "original_mode": None})

    assert stat.S_IMODE(target.stat().st_mode) == 0o444


def test_query_macos_missing_file(tmp_path) -> None:
    level, backend = query_macos(str(tmp_path / "gone.txt"))
    assert level == "MISSING"
    assert backend is None


def test_query_macos_readonly_mode_without_flag(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)
    level, backend = query_macos(str(target))
    assert level == "READONLY"
    assert backend == "macos-chmod"


def test_query_macos_unprotected(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o644)
    level, backend = query_macos(str(target))
    assert level == "UNPROTECTED"
    assert backend is None
