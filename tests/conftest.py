"""Shared pytest fixtures (SPEC.md 27).

`patch_posix_backend` swaps every module-level `protect_path` /
`unprotect_path` / `query_path` reference (bound at import time via
`from chwrite.backends import ...`, so patching `chwrite.backends.*` alone
would not affect already-imported names) with the real chmod-based
`posix_generic` backend implementation, regardless of the host OS.

This lets cli.py/reconcile.py logic (locking bookkeeping, idempotency,
report events, state transitions) be exercised deterministically without
depending on macOS's real chflags uchg immutability - which, if a test
fails mid-way, would leave an undeletable file behind under pytest's
tmp_path (chflags uchg blocks unlink, not just write) and slowly pollute
the temp directory across runs. The real macOS backend already gets a
dedicated, careful real-OS test in tests/backends/test_macos.py (SPEC.md
23, 27); everything else only needs *a* working backend to exercise its
own logic correctly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chwrite.backends.posix_generic import protect_posix, query_posix, unprotect_posix


def _query_posix_entry_aware(full_path: str, entry: object = None) -> tuple[str, str | None]:
    """Adapt query_posix's (full_path) signature to query_path's
    (full_path, entry=None) one - posix_generic has no scoped backend
    (SPEC.md section 29 only covers macOS/Linux/Windows), so `entry` is
    accepted but ignored, same as chwrite.backends.query_path falling
    through to the blanket backend on platforms outside SCOPED_BACKENDS.
    """
    del entry
    return query_posix(full_path)


@pytest.fixture
def posix_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force cli.py/diagnostics.py/reconcile.py to use the chmod-only
    posix_generic backend. query_path is only referenced by diagnostics.py
    and reconcile.py (cli.py's own subcommands - init/add/remove/apply/
    lock/unlock/unlocked - never call it directly; status/verify moved to
    diagnostics.py to stay under the 600-line source-file ceiling, SPEC.md
    section 26)."""
    for mod_name in ("chwrite.cli", "chwrite.reconcile"):
        monkeypatch.setattr(f"{mod_name}.protect_path", protect_posix)
        monkeypatch.setattr(f"{mod_name}.unprotect_path", unprotect_posix)
    for mod_name in ("chwrite.diagnostics", "chwrite.reconcile"):
        monkeypatch.setattr(f"{mod_name}.query_path", _query_posix_entry_aware)


def init_repo(tmp_path: Path) -> str:
    """Initialize a throwaway git repo at tmp_path and return its str path."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return str(tmp_path)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway git repo at tmp_path, with cwd chdir'd into it."""
    root = init_repo(tmp_path)
    monkeypatch.chdir(root)
    return root
