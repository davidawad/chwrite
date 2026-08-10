"""Linux scoped (deny-user/deny-group POSIX ACL) backend tests against
mocked setfacl/getfacl (SPEC.md 29, 29.1, 27).

Can't exercise real setfacl/getfacl on this macOS dev box - these verify
argument-array construction, the "refuse, never fall back" behavior when
the acl tooling is missing, and getfacl-output-derived query state.
"""

from __future__ import annotations

import stat
from unittest.mock import MagicMock, patch

import pytest

from chwrite.backends.linux import (
    _acl_tools_available,
    linux_acl_capability,
    protect_linux_scoped,
    query_linux_scoped,
    unprotect_linux_scoped,
)
from chwrite.errors import ChwriteError


def _which_both(name: str) -> str | None:
    return f"/usr/bin/{name}" if name in ("setfacl", "getfacl") else None


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_scoped_deny_user_calls_setfacl(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_linux_scoped(str(target), ["ci-bot"], [])

    mock_run.assert_called_once_with(
        ["setfacl", "-m", "u:ci-bot:0", str(target)], capture_output=True, check=False
    )
    assert result == {
        "backend": "linux-acl-deny",
        "level": "ENFORCED",
        "hard": False,
        "acl_entries": ["u:ci-bot"],
    }


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_scoped_deny_group_calls_setfacl(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_linux_scoped(str(target), [], ["contractors"])

    mock_run.assert_called_once_with(
        ["setfacl", "-m", "g:contractors:0", str(target)], capture_output=True, check=False
    )
    assert result["acl_entries"] == ["g:contractors"]


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_scoped_multiple_names_all_applied(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_linux_scoped(str(target), ["bob", "alice"], ["contractors"])

    assert mock_run.call_count == 3
    assert result["acl_entries"] == ["u:bob", "u:alice", "g:contractors"]


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_protect_linux_scoped_without_acl_package_refuses(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")

    with pytest.raises(ChwriteError) as exc_info:
        protect_linux_scoped(str(target), ["bob"], [])
    assert exc_info.value.code == 2
    assert "acl" in str(exc_info.value)


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
def test_protect_linux_scoped_never_falls_back_to_blanket(_which, tmp_path) -> None:
    """Even if setfacl is missing, protect_linux_scoped must not silently
    apply a blanket chmod-based protection instead (SPEC.md 29.1)."""
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o644)
    with (
        patch("chwrite.backends.linux.shutil.which", return_value=None),
        pytest.raises(ChwriteError),
    ):
        protect_linux_scoped(str(target), ["bob"], [])
    # mode bits untouched - no blanket fallback happened
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_scoped_setfacl_failure_raises_with_context(
    mock_run, _which, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"Operation not supported")

    with pytest.raises(ChwriteError) as exc_info:
        protect_linux_scoped(str(target), ["bob"], [])
    assert "bob" in str(exc_info.value)
    assert "ACL support" in str(exc_info.value) or "mounted" in str(exc_info.value)


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_scoped_group_failure_mentions_group_never_created(
    mock_run, _which, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"Invalid argument")

    with pytest.raises(ChwriteError) as exc_info:
        protect_linux_scoped(str(target), [], ["ghost-group"])
    assert "ghost-group" in str(exc_info.value)
    assert "never creates" in str(exc_info.value)


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_unprotect_linux_scoped_calls_setfacl_x_per_entry(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    unprotect_linux_scoped(str(target), {"acl_entries": ["u:bob", "g:contractors"]})

    assert mock_run.call_args_list[0].args[0] == ["setfacl", "-x", "u:bob", str(target)]
    assert mock_run.call_args_list[1].args[0] == ["setfacl", "-x", "g:contractors", str(target)]


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_unprotect_linux_scoped_without_setfacl_is_a_noop(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    unprotect_linux_scoped(str(target), {"acl_entries": ["u:bob"]})  # must not raise


def test_unprotect_linux_scoped_missing_file_is_a_noop() -> None:
    unprotect_linux_scoped("/nonexistent/path.txt", {"acl_entries": ["u:bob"]})


def test_query_linux_scoped_missing_file(tmp_path) -> None:
    level, backend = query_linux_scoped(str(tmp_path / "gone.txt"), ["bob"], [])
    assert level == "MISSING"
    assert backend is None


def test_query_linux_scoped_no_names_is_unprotected(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    level, backend = query_linux_scoped(str(target), [], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_query_linux_scoped_without_getfacl_is_unprotected(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    level, backend = query_linux_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_query_linux_scoped_detects_deny_entries(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            b"# file: f.txt\n# owner: me\n# group: me\nuser::rw-\nuser:bob:---\n"
            b"group::r--\ngroup:contractors:---\nmask::rw-\nother::r--\n"
        ),
    )

    level, backend = query_linux_scoped(str(target), ["bob"], ["contractors"])
    assert level == "ENFORCED"
    assert backend == "linux-acl-deny"


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_query_linux_scoped_missing_entry_is_unprotected(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0, stdout=b"user::rw-\ngroup::r--\nother::r--\n")

    level, backend = query_linux_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
@patch("chwrite.backends.linux.subprocess.run")
def test_query_linux_scoped_getfacl_failure_is_unprotected(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stdout=b"")

    level, backend = query_linux_scoped(str(target), ["bob"], [])
    assert level == "UNPROTECTED"
    assert backend is None


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
def test_acl_tools_available_true_when_both_found(_which) -> None:
    assert _acl_tools_available() is True


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_acl_tools_available_false_when_missing(_which) -> None:
    assert _acl_tools_available() is False


@patch("chwrite.backends.linux.shutil.which", side_effect=_which_both)
def test_linux_acl_capability_available_message(_which) -> None:
    assert "available" in linux_acl_capability()
    assert "NOT" not in linux_acl_capability()


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_linux_acl_capability_not_available_message(_which) -> None:
    assert "NOT available" in linux_acl_capability()
