"""Generic POSIX fallback backend for unrecognized Unix-likes (SPEC.md 13).

Also home of the shared chmod-readonly helper used by the macOS and Linux
backends for their unprivileged fallback path.
"""

from __future__ import annotations

import os
import stat
import sys

from chwrite.state import FileEntry, ProtectResult


def chmod_readonly(full_path: str) -> None:
    """Strip write bits while preserving read/execute bits."""
    mode = stat.S_IMODE(os.lstat(full_path).st_mode)
    os.chmod(full_path, mode & ~0o222)


def protect_posix(full_path: str, hard: bool) -> ProtectResult:
    """Apply chmod a-w. Classified READONLY (SPEC.md section 13)."""
    if hard:
        sys.stderr.write(
            "note: HARD protection is only implemented for Linux (chattr) in this version; "
            "applying READONLY instead.\n"
        )
    chmod_readonly(full_path)
    return {"backend": "posix-chmod", "level": "READONLY", "hard": False}


def unprotect_posix(full_path: str, entry: FileEntry) -> None:
    """Restore the recorded original mode."""
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        os.chmod(full_path, original_mode)


def query_posix(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state - never trust state.json alone."""
    try:
        st = os.lstat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "posix-chmod"
    return "UNPROTECTED", None
