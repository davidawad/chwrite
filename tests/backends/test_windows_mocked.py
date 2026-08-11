"""Windows backend unit tests against mocked subprocess.run (SPEC.md 27).

Can't exercise real icacls.exe on this macOS dev box - these verify
argument-array construction and the deny-ACE-only unlock path.
"""

from __future__ import annotations

import stat
from unittest.mock import MagicMock, patch

from chwrite.backends.windows import (
    _icacls_path,
    _windows_username,
    protect_windows,
    query_windows,
    unprotect_windows,
)


@patch("chwrite.backends.windows._windows_username", return_value="dave")
@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_protect_windows_calls_icacls_deny(mock_run, _icacls, _user, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_windows(str(target), hard=False)

    mock_run.assert_called_once_with(
        ["icacls", str(target), "/deny", "dave:(WD,AD,WEA,WA)"], capture_output=True, check=False
    )
    assert result == {
        "backend": "windows-acl",
        "level": "ENFORCED",
        "hard": False,
        "acl_user": "dave",
    }


@patch("chwrite.backends.windows._windows_username", return_value="dave")
@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_unprotect_windows_removes_only_the_deny_ace(mock_run, _icacls, _user, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    unprotect_windows(
        str(target), {"backend": "windows-acl", "acl_user": "dave", "original_mode": None}
    )

    mock_run.assert_called_once_with(
        ["icacls", str(target), "/remove:d", "dave"], capture_output=True, check=False
    )


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_protect_windows_falls_back_to_readonly_when_no_icacls(_icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")

    result = protect_windows(str(target), hard=False)

    assert result == {"backend": "windows-readonly", "level": "READONLY", "hard": False}


@patch("chwrite.backends.windows._windows_username", return_value="dave")
@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_protect_windows_hard_prints_note_and_still_applies_enforced(
    mock_run, _icacls, _user, tmp_path, capsys
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_windows(str(target), hard=True)

    assert result["level"] == "ENFORCED"
    assert result["hard"] is False
    assert "not available on Windows" in capsys.readouterr().err


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_unprotect_windows_without_icacls_restores_mode_only(_icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)

    unprotect_windows(str(target), {"backend": "windows-readonly", "original_mode": 0o644})

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_query_windows_missing_file(tmp_path) -> None:

    level, backend = query_windows(str(tmp_path / "does-not-exist.txt"))
    assert level == "MISSING"
    assert backend is None


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_query_windows_detects_deny_ace(mock_run, _icacls, tmp_path) -> None:

    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0, stdout=b"Everyone:(DENY)(W)")

    level, backend = query_windows(str(target))
    assert level == "ENFORCED"
    assert backend == "windows-acl"


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_query_windows_readonly_via_mode_bits(_icacls, tmp_path) -> None:

    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)

    level, backend = query_windows(str(target))
    assert level == "READONLY"
    assert backend == "windows-readonly"


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_query_windows_unprotected(_icacls, tmp_path) -> None:

    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o644)

    level, backend = query_windows(str(target))
    assert level == "UNPROTECTED"
    assert backend is None


def test_windows_username_env_fallback(monkeypatch) -> None:

    monkeypatch.setenv("USERNAME", "envuser")
    assert _windows_username() == "envuser"


def test_windows_username_getpass_fallback(monkeypatch) -> None:

    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.setattr("chwrite.backends.windows.getpass.getuser", lambda: "getpassuser")
    assert _windows_username() == "getpassuser"


def test_icacls_path_uses_shutil_which(monkeypatch) -> None:

    monkeypatch.setattr(
        "chwrite.backends.windows.shutil.which",
        lambda name: "C:\\Windows\\System32\\icacls.exe" if name == "icacls.exe" else None,
    )
    assert _icacls_path() == "C:\\Windows\\System32\\icacls.exe"
