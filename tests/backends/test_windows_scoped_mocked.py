"""Windows scoped (deny-user/deny-group icacls) backend tests against
mocked subprocess.run (SPEC.md 29, 27).

Can't exercise real icacls.exe on this macOS dev box - these verify
argument-array construction and the "refuse, never fall back" behavior.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chwrite.backends.windows import (
    protect_windows_scoped,
    query_windows_scoped,
    unprotect_windows_scoped,
)
from chwrite.errors import ChwriteError


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_protect_windows_scoped_deny_user_calls_icacls_deny(mock_run, _icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_windows_scoped(str(target), ["ci-bot"], [])

    mock_run.assert_called_once_with(
        ["icacls", str(target), "/deny", "ci-bot:(WD,AD,WEA,WA)"], capture_output=True, check=False
    )
    assert result == {
        "backend": "windows-acl-deny",
        "level": "ENFORCED",
        "hard": False,
        "acl_entries": ["ci-bot"],
    }


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_protect_windows_scoped_deny_group_calls_icacls_deny(mock_run, _icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_windows_scoped(str(target), [], ["contractors"])

    mock_run.assert_called_once_with(
        ["icacls", str(target), "/deny", "contractors:(WD,AD,WEA,WA)"],
        capture_output=True,
        check=False,
    )
    assert result["acl_entries"] == ["contractors"]


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_protect_windows_scoped_multiple_names(mock_run, _icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_windows_scoped(str(target), ["bob", "alice"], ["contractors"])

    assert mock_run.call_count == 3
    assert result["acl_entries"] == ["bob", "alice", "contractors"]


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_protect_windows_scoped_without_icacls_refuses(_icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    with pytest.raises(ChwriteError) as exc_info:
        protect_windows_scoped(str(target), ["bob"], [])
    assert exc_info.value.code == 2


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_protect_windows_scoped_icacls_failure_raises_with_context(
    mock_run, _icacls, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"no such user or group")

    with pytest.raises(ChwriteError) as exc_info:
        protect_windows_scoped(str(target), ["ghost"], [])
    assert "ghost" in str(exc_info.value)
    assert "never creates" in str(exc_info.value)


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_unprotect_windows_scoped_removes_each_deny_ace(mock_run, _icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    unprotect_windows_scoped(str(target), {"acl_entries": ["bob", "contractors"]})

    assert mock_run.call_args_list[0].args[0] == ["icacls", str(target), "/remove:d", "bob"]
    assert mock_run.call_args_list[1].args[0] == [
        "icacls",
        str(target),
        "/remove:d",
        "contractors",
    ]


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_unprotect_windows_scoped_without_icacls_is_a_noop(_icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    unprotect_windows_scoped(str(target), {"acl_entries": ["bob"]})  # must not raise


def test_query_windows_scoped_missing_file(tmp_path) -> None:
    level, backend = query_windows_scoped(str(tmp_path / "gone.txt"), ["bob"], [])
    assert level == "MISSING"
    assert backend is None


def test_query_windows_scoped_no_names_is_unprotected(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    level, backend = query_windows_scoped(str(target), [], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_query_windows_scoped_detects_deny_entries(mock_run, _icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(
        returncode=0, stdout=b"BUILTIN\\bob:(DENY)(W)\nEveryone:(R)\n"
    )

    level, backend = query_windows_scoped(str(target), ["bob"], [])
    assert level == "ENFORCED"
    assert backend == "windows-acl-deny"


@patch("chwrite.backends.windows._icacls_path", return_value="icacls")
@patch("chwrite.backends.windows.subprocess.run")
def test_query_windows_scoped_missing_entry_is_unprotected(mock_run, _icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0, stdout=b"Everyone:(R)\n")

    level, backend = query_windows_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.windows._icacls_path", return_value=None)
def test_query_windows_scoped_without_icacls_is_unprotected(_icacls, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    level, backend = query_windows_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None
