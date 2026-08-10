"""macOS scoped (deny-user/deny-group ACL) backend tests against mocked
chmod +a/-a (SPEC.md 29, 29.1, 27).

Complements tests/backends/test_macos_scoped.py's real end-to-end test with
the branches that need to *not* have working chmod on this box (missing
chmod, chmod +a failure) and precise argument-array assertions.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chwrite.backends.macos import (
    protect_macos_scoped,
    query_macos_scoped,
    unprotect_macos_scoped,
)
from chwrite.errors import ChwriteError


@patch("chwrite.backends.macos.shutil.which", return_value="/bin/chmod")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_scoped_deny_user_calls_chmod_plus_a(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_macos_scoped(str(target), ["ci-bot"], [])

    mock_run.assert_called_once_with(
        ["/bin/chmod", "+a", "user:ci-bot deny write,delete,append,writeattr,chown", str(target)],
        capture_output=True,
        check=False,
    )
    assert result == {
        "backend": "macos-acl-deny",
        "level": "ENFORCED",
        "hard": False,
        "acl_entries": ["user:ci-bot deny write,delete,append,writeattr,chown"],
    }


@patch("chwrite.backends.macos.shutil.which", return_value="/bin/chmod")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_scoped_deny_group_calls_chmod_plus_a(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_macos_scoped(str(target), [], ["contractors"])

    mock_run.assert_called_once_with(
        [
            "/bin/chmod",
            "+a",
            "group:contractors deny write,delete,append,writeattr,chown",
            str(target),
        ],
        capture_output=True,
        check=False,
    )
    assert result["acl_entries"] == ["group:contractors deny write,delete,append,writeattr,chown"]


@patch("chwrite.backends.macos.shutil.which", return_value="/bin/chmod")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_scoped_multiple_users_and_groups(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_macos_scoped(str(target), ["bob", "alice"], ["contractors"])

    assert mock_run.call_count == 3
    assert result["acl_entries"] == [
        "user:bob deny write,delete,append,writeattr,chown",
        "user:alice deny write,delete,append,writeattr,chown",
        "group:contractors deny write,delete,append,writeattr,chown",
    ]


@patch("chwrite.backends.macos.os.path.exists", return_value=False)
@patch("chwrite.backends.macos.shutil.which", return_value=None)
def test_protect_macos_scoped_without_chmod_raises(_which, _exists, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    with pytest.raises(ChwriteError) as exc_info:
        protect_macos_scoped(str(target), ["bob"], [])
    assert exc_info.value.code == 2


@patch("chwrite.backends.macos.shutil.which", return_value="/bin/chmod")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_scoped_chmod_failure_raises_never_falls_back(
    mock_run, _which, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"Operation not permitted")

    with pytest.raises(ChwriteError) as exc_info:
        protect_macos_scoped(str(target), ["bob"], [])
    assert exc_info.value.code == 2
    assert "bob" in str(exc_info.value)


@patch("chwrite.backends.macos.shutil.which", return_value="/bin/chmod")
@patch("chwrite.backends.macos.subprocess.run")
def test_protect_macos_scoped_group_failure_mentions_group_never_created(
    mock_run, _which, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"no such group")

    with pytest.raises(ChwriteError) as exc_info:
        protect_macos_scoped(str(target), [], ["ghost-group"])
    assert "ghost-group" in str(exc_info.value)
    assert "never creates" in str(exc_info.value)


@patch("chwrite.backends.macos.shutil.which", return_value="/bin/chmod")
@patch("chwrite.backends.macos.subprocess.run")
def test_unprotect_macos_scoped_removes_each_ace_in_reverse(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    entry = {
        "backend": "macos-acl-deny",
        "original_mode": None,
        "acl_entries": ["user:bob deny write,delete,append,writeattr,chown", "group:x deny y"],
    }
    unprotect_macos_scoped(str(target), entry)

    assert mock_run.call_args_list[0].args[0] == ["/bin/chmod", "-a", "group:x deny y", str(target)]
    assert mock_run.call_args_list[1].args[0] == [
        "/bin/chmod",
        "-a",
        "user:bob deny write,delete,append,writeattr,chown",
        str(target),
    ]


def test_unprotect_macos_scoped_missing_file_is_a_noop(tmp_path) -> None:
    unprotect_macos_scoped(str(tmp_path / "gone.txt"), {"acl_entries": ["user:bob deny write"]})


def test_query_macos_scoped_missing_file(tmp_path) -> None:
    level, backend = query_macos_scoped(str(tmp_path / "gone.txt"), ["bob"], [])
    assert level == "MISSING"
    assert backend is None


def test_query_macos_scoped_no_names_is_unprotected(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    level, backend = query_macos_scoped(str(target), [], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.macos.subprocess.run")
def test_query_macos_scoped_detects_deny_entry(mock_run, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(
        returncode=0, stdout=b" 0: user:bob deny write,delete,append,writeattr,chown\n"
    )

    level, backend = query_macos_scoped(str(target), ["bob"], [])
    assert level == "ENFORCED"
    assert backend == "macos-acl-deny"


@patch("chwrite.backends.macos.subprocess.run")
def test_query_macos_scoped_missing_entry_is_unprotected(mock_run, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0, stdout=b"")

    level, backend = query_macos_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.macos.subprocess.run")
def test_query_macos_scoped_ls_failure_is_unprotected(mock_run, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stdout=b"")

    level, backend = query_macos_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None
