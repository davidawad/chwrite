"""Fallback for operating systems with no known protection primitive.

SPEC.md section 14: chwrite remains usable rather than failing outright -
it reports VERIFY (detection-only, no write prevention) instead of
pretending to offer stronger protection it cannot deliver.
"""

from __future__ import annotations

import os

from chwrite.state import FileEntry, ProtectResult


def protect_unknown(full_path: str, hard: bool) -> ProtectResult:
    """No-op: nothing to apply. Classified VERIFY. `hard` is unused - this
    backend never offers HARD, kept only for the uniform backend signature."""
    del hard
    return {"backend": "verify-only", "level": "VERIFY", "hard": False}


def unprotect_unknown(full_path: str, entry: FileEntry) -> None:
    """No-op: nothing was applied. `entry` unused, kept for a uniform signature."""
    del entry


def query_unknown(full_path: str) -> tuple[str, str | None]:
    """VERIFY for any existing file; MISSING if it has been deleted."""
    if not os.path.exists(full_path):
        return "MISSING", None
    return "VERIFY", "verify-only"
