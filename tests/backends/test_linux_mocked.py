"""Linux backend unit tests against mocked subprocess.run (SPEC.md 27).

Can't exercise real chattr/lsattr on this macOS dev box - these verify
argument-array construction, exit-code handling, and that a chattr
privilege failure prints the sudo command rather than invoking it.
"""

from __future__ import annotations

import stat
from unittest.mock import MagicMock, patch

from chwrite.backends.linux import protect_linux, query_linux, unprotect_linux
from chwrite.errors import ChwriteError


def test_protect_linux_default_uses_chmod(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    result = protect_linux(str(target), hard=False)
    assert result == {"backend": "linux-chmod", "level": "READONLY", "hard": False}


@patch("chwrite.backends.linux.shutil.which", return_value="/usr/bin/chattr")
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_hard_calls_chattr_with_argument_array(
    mock_run, _mock_which, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    result = protect_linux(str(target), hard=True)

    mock_run.assert_called_once_with(
        ["chattr", "+i", str(target)], capture_output=True, check=False
    )
    assert result == {"backend": "linux-chattr", "level": "HARD", "hard": True}


@patch("chwrite.backends.linux.shutil.which", return_value="/usr/bin/chattr")
@patch("chwrite.backends.linux.subprocess.run")
def test_protect_linux_hard_failure_never_invokes_sudo(
    mock_run, _mock_which, tmp_path, capsys
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"Operation not permitted")

    result = protect_linux(str(target), hard=True)

    for call in mock_run.call_args_list:
        assert "sudo" not in call.args[0]
    assert result["backend"] == "linux-chmod"
    assert result["level"] == "READONLY"
    stderr = capsys.readouterr().err
    assert "sudo chwrite lock --hard" in stderr


@patch("chwrite.backends.linux.shutil.which", return_value="/usr/bin/chattr")
@patch("chwrite.backends.linux.subprocess.run")
def test_unprotect_linux_calls_chattr_minus_i(mock_run, _mock_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0)

    unprotect_linux(str(target), {"backend": "linux-chattr", "original_mode": 0o644})

    mock_run.assert_called_once_with(
        ["chattr", "-i", str(target)], capture_output=True, check=False
    )


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_protect_linux_hard_without_chattr_warns_and_falls_back(_which, tmp_path, capsys) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")

    result = protect_linux(str(target), hard=True)

    assert result["backend"] == "linux-chmod"
    assert "chattr not found" in capsys.readouterr().err


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_unprotect_linux_without_chattr_and_no_chattr_backend_just_restores_mode(
    _which, tmp_path
) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)

    unprotect_linux(str(target), {"backend": "linux-chmod", "original_mode": 0o644})

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_unprotect_linux_chattr_backend_without_chattr_binary_raises(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")

    try:
        unprotect_linux(str(target), {"backend": "linux-chattr", "original_mode": 0o644})
        raise AssertionError("expected ChwriteError")
    except ChwriteError as e:
        assert e.code == 2


@patch("chwrite.backends.linux.shutil.which", return_value="/usr/bin/chattr")
@patch("chwrite.backends.linux.subprocess.run")
def test_unprotect_linux_chattr_failure_raises(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=1, stderr=b"Operation not permitted")

    try:
        unprotect_linux(str(target), {"backend": "linux-chattr", "original_mode": 0o644})
        raise AssertionError("expected ChwriteError")
    except ChwriteError as e:
        assert e.code == 2
        assert "sudo chattr -i" in str(e)


def test_query_linux_missing_file(tmp_path) -> None:
    level, backend = query_linux(str(tmp_path / "gone.txt"))
    assert level == "MISSING"
    assert backend is None


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_query_linux_readonly_without_lsattr(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o444)

    level, backend = query_linux(str(target))
    assert level == "READONLY"
    assert backend == "linux-chmod"


@patch("chwrite.backends.linux.shutil.which", return_value="/usr/bin/lsattr")
@patch("chwrite.backends.linux.subprocess.run")
def test_query_linux_detects_immutable_via_lsattr(mock_run, _which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    mock_run.return_value = MagicMock(returncode=0, stdout=b"----i--------e-- f.txt")

    level, backend = query_linux(str(target))
    assert level == "HARD"
    assert backend == "linux-chattr"


@patch("chwrite.backends.linux.shutil.which", return_value=None)
def test_query_linux_unprotected(_which, tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    target.chmod(0o644)

    level, backend = query_linux(str(target))
    assert level == "UNPROTECTED"
    assert backend is None
