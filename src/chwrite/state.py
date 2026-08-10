"""Local runtime state: .git/chwrite/state.json (SPEC.md section 9).

Never committed. Records enough information to restore each protected
file's original state. `status`/`verify` must not rely on this alone to
decide whether a file is *currently* protected - they inspect real OS
state via chwrite.backends - but it is the source of truth for what
chwrite believes it applied and how to undo it.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Sequence
from typing import Any, Literal, NotRequired, TypedDict

from chwrite.errors import ChwriteError
from chwrite.gitutil import run_git

STATE_VERSION = 1


class ScopeDict(TypedDict):
    """A narrowed deny-user/deny-group restriction (SPEC.md section 29)."""

    deny_user: list[str]
    deny_group: list[str]


# "all" is the default blanket-block mode (sections 10-14, unchanged behavior).
# A ScopeDict means the protection only denies the named identities.
Scope = Literal["all"] | ScopeDict


def make_scope(deny_user: Sequence[str], deny_group: Sequence[str]) -> Scope:
    """Build the state.json scope value for a set of deny-user/deny-group names."""
    if not deny_user and not deny_group:
        return "all"
    return {"deny_user": list(deny_user), "deny_group": list(deny_group)}


def scope_deny_user(scope: Scope) -> list[str]:
    """The deny-user names for a scope, or [] for blanket/absent scope."""
    if scope == "all":
        return []
    return scope.get("deny_user", [])


def scope_deny_group(scope: Scope) -> list[str]:
    """The deny-group names for a scope, or [] for blanket/absent scope."""
    if scope == "all":
        return []
    return scope.get("deny_group", [])


class ProtectResult(TypedDict):
    """What a backend's protect_*() returns: just what it actually did.

    Deliberately smaller than FileEntry - a backend has no opinion on
    source/message/original_mode, which are the caller's bookkeeping.
    """

    backend: str
    level: str
    hard: bool
    acl_user: NotRequired[str]
    # The exact ACE/ACL descriptor(s) applied by a *scoped* backend (macOS
    # `chmod +a` argument, Linux `u:<name>`/`g:<name>` setfacl targets,
    # Windows icacls names) - recorded so unprotect can remove precisely
    # what was added, per identity (SPEC.md section 29).
    acl_entries: NotRequired[list[str]]


class FileEntry(TypedDict):
    """One protected-file record inside state.json."""

    backend: str
    level: str
    original_mode: int | None
    locked: bool
    source: str  # "policy" | "adhoc"
    message: str
    hard: bool
    acl_user: NotRequired[str]
    acl_entries: NotRequired[list[str]]
    # Absent/"all" = blanket block (sections 10-14). A ScopeDict means this
    # file is only denied to specific users/groups (section 29). status/
    # doctor treat this as advisory bookkeeping only - they re-derive the
    # real state from the OS, same as everywhere else in this module.
    scope: NotRequired[Scope]


class StateDoc(TypedDict):
    """The full contents of state.json."""

    version: int
    files: dict[str, FileEntry]


def state_paths(root: str) -> tuple[str, str]:
    """Return (state_dir, state_file) for the repo at root.

    Resolved via `git rev-parse --git-dir` (not a hardcoded ".git/") so
    this is correct for worktrees and repos with a non-default git dir.
    """
    proc = run_git(["rev-parse", "--git-dir"], cwd=root)
    git_dir = proc.stdout.decode(errors="replace").strip()
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(root, git_dir)
    git_dir = os.path.realpath(git_dir)
    state_dir = os.path.join(git_dir, "chwrite")
    return state_dir, os.path.join(state_dir, "state.json")


def load_state(root: str) -> StateDoc:
    """Load state.json, or an empty document if it does not exist yet."""
    _, state_file = state_paths(root)
    if not os.path.isfile(state_file):
        return {"version": STATE_VERSION, "files": {}}
    try:
        with open(state_file, encoding="utf-8") as f:
            data: Any = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ChwriteError(f"corrupt chwrite state file {state_file}: {e}", 2) from e
    if not isinstance(data, dict) or "files" not in data or not isinstance(data["files"], dict):
        raise ChwriteError(f"corrupt chwrite state file {state_file}: missing 'files'", 2)
    return data  # type: ignore[no-any-return]


def save_state(root: str, data: StateDoc) -> None:
    """Atomically write state.json (write to temp file, then rename)."""
    state_dir, state_file = state_paths(root)
    os.makedirs(state_dir, exist_ok=True)
    tmp = state_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, state_file)


def determine_original_mode(full_path: str, entry: FileEntry | None) -> int | None:
    """The POSIX mode to restore on unlock: reuse a stored value if present.

    Only reads the file's *current* mode when no prior value is recorded,
    since re-deriving it from an already-protected file would capture the
    protected (e.g. read-only) mode instead of the true original one.
    """
    if entry and entry.get("original_mode") is not None:
        return entry["original_mode"]
    try:
        return stat.S_IMODE(os.lstat(full_path).st_mode)
    except FileNotFoundError:
        return None
