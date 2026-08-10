"""Real macOS deny-user ACL backend end-to-end test (SPEC.md 29, 27) - not
mocked. Skipped outside macOS.

Uses deny-user=<current user> against a file we own, in a throwaway repo
under pytest's tmp_path: this is the only scoped-lock scenario testable
without root/a second local user (see SPEC.md 29's note that a
non-owner-vs-owner distinction only matters for Linux POSIX ACLs, not
macOS NFSv4-style ACEs - on macOS the deny ACE applies to the named
identity's *data-write* right regardless of ownership, so denying our own
username here is a faithful exercise of the real enforcement path, not a
weaker approximation of it).
"""

from __future__ import annotations

import getpass
import subprocess
import sys

import pytest

from chwrite.backends.macos import (
    protect_macos_scoped,
    query_macos_scoped,
    unprotect_macos_scoped,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="exercises the real macOS chmod +a ACL backend"
)


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "protected.txt").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)


def test_deny_user_self_blocks_own_write_and_unlock_restores_it(tmp_path) -> None:
    """The literal scenario SPEC.md 29's testing note asks for: `chwrite
    lock --deny-user $(whoami)` against a throwaway file, confirming the
    write now fails, then confirming unlock restores it."""
    _init_repo(tmp_path)
    target = tmp_path / "protected.txt"
    me = getpass.getuser()

    result = protect_macos_scoped(str(target), [me], [])
    assert result["level"] == "ENFORCED"
    assert result["backend"] == "macos-acl-deny"

    with pytest.raises(PermissionError):
        target.write_text("changed by self")

    level, backend = query_macos_scoped(str(target), [me], [])
    assert level == "ENFORCED"
    assert backend == "macos-acl-deny"

    unprotect_macos_scoped(str(target), {"acl_entries": result["acl_entries"]})
    target.write_text("changed by self")
    assert target.read_text() == "changed by self"

    level, backend = query_macos_scoped(str(target), [me], [])
    assert level == "UNPROTECTED"


def test_deny_group_self_primary_group_blocks_write(tmp_path) -> None:
    """Same mechanic, but via the deny-group path (SPEC.md 29's macOS
    section covers both user and group ACEs identically)."""
    _init_repo(tmp_path)
    target = tmp_path / "protected.txt"
    proc = subprocess.run(["id", "-gn"], capture_output=True, check=True, text=True)
    my_group = proc.stdout.strip()

    result = protect_macos_scoped(str(target), [], [my_group])
    assert result["level"] == "ENFORCED"

    with pytest.raises(PermissionError):
        target.write_text("changed by group member")

    unprotect_macos_scoped(str(target), {"acl_entries": result["acl_entries"]})
    target.write_text("changed by group member")
    assert target.read_text() == "changed by group member"


def test_protect_then_reprotect_is_idempotent_in_effect(tmp_path) -> None:
    _init_repo(tmp_path)
    target = tmp_path / "protected.txt"
    me = getpass.getuser()

    protect_macos_scoped(str(target), [me], [])
    result2 = protect_macos_scoped(str(target), [me], [])
    with pytest.raises(PermissionError):
        target.write_text("nope")

    # Two ACEs were added (chmod +a doesn't dedupe); removing both restores
    # write access, confirming unprotect handles the acl_entries list as
    # recorded rather than assuming exactly one entry.
    all_entries = result2["acl_entries"] * 2
    unprotect_macos_scoped(str(target), {"acl_entries": all_entries})
    target.write_text("restored")
    assert target.read_text() == "restored"
