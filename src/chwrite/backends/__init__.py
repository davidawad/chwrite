"""Backend selection by platform.system()/sys.platform (SPEC.md 10-14, 29).

Exposes protect_path/unprotect_path/query_path (blanket-block, sections
10-14) and protect_path_scoped/query_path_scoped (deny-user/deny-group
narrowed locks, section 29), dispatching to whichever per-OS backend
module matches the current platform. Each backend module is self-contained
(no cross-backend imports) so unit tests can exercise, say, the Linux
backend's argument-array construction on macOS CI by mocking
subprocess.run directly.
"""

from __future__ import annotations

import os
import platform

from chwrite.backends.linux import (
    linux_acl_capability,
    protect_linux,
    protect_linux_scoped,
    query_linux,
    query_linux_scoped,
    unprotect_linux,
    unprotect_linux_scoped,
)
from chwrite.backends.macos import (
    protect_macos,
    protect_macos_scoped,
    query_macos,
    query_macos_scoped,
    unprotect_macos,
    unprotect_macos_scoped,
)
from chwrite.backends.posix_generic import protect_posix, query_posix, unprotect_posix
from chwrite.backends.unknown import protect_unknown, query_unknown, unprotect_unknown
from chwrite.backends.windows import (
    protect_windows,
    protect_windows_scoped,
    query_windows,
    query_windows_scoped,
    unprotect_windows,
    unprotect_windows_scoped,
)
from chwrite.errors import ChwriteError
from chwrite.state import FileEntry, ProtectResult, scope_deny_group, scope_deny_user

__all__ = [
    "BACKENDS",
    "PLATFORM",
    "SCOPED_BACKENDS",
    "detect_platform",
    "linux_acl_capability",
    "protect_path",
    "protect_path_scoped",
    "query_path",
    "unprotect_path",
]


def detect_platform() -> str:
    """Map platform.system()/os.name to one of chwrite's five backends."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "linux"
    if system == "Windows":
        return "windows"
    if os.name == "posix":
        return "posix"
    return "unknown"


PLATFORM = detect_platform()

BACKENDS = {
    "macos": (protect_macos, unprotect_macos, query_macos),
    "linux": (protect_linux, unprotect_linux, query_linux),
    "windows": (protect_windows, unprotect_windows, query_windows),
    "posix": (protect_posix, unprotect_posix, query_posix),
    "unknown": (protect_unknown, unprotect_unknown, query_unknown),
}

# Only macOS, Linux, and Windows have a real per-identity deny primitive
# (SPEC.md section 29's 29.1: NFSv4-style ACL / POSIX ACL / NTFS ACL,
# respectively). The generic POSIX chmod fallback and the VERIFY-only
# unknown-OS backend have no such mechanism, so they are deliberately
# absent here - protect_path_scoped() refuses rather than pretending.
SCOPED_BACKENDS = {
    "macos": (protect_macos_scoped, unprotect_macos_scoped, query_macos_scoped),
    "linux": (protect_linux_scoped, unprotect_linux_scoped, query_linux_scoped),
    "windows": (protect_windows_scoped, unprotect_windows_scoped, query_windows_scoped),
}


def protect_path(full_path: str, hard: bool = False) -> ProtectResult:
    """Apply the current platform's strongest available blanket protection."""
    return BACKENDS[PLATFORM][0](full_path, hard)


def protect_path_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply a deny-user/deny-group scoped lock (SPEC.md section 29).

    Raises:
        ChwriteError: this platform has no real deny-ACE primitive, or the
            platform-specific tooling/capability required is unavailable.
            Never silently falls back to a blanket block or no-op.
    """
    if PLATFORM not in SCOPED_BACKENDS:
        raise ChwriteError(
            f"deny-user/deny-group scoped locks are not implemented on this platform "
            f"({PLATFORM}); only macOS, Linux, and Windows provide a real per-identity deny "
            "primitive (SPEC.md section 29)",
            2,
        )
    return SCOPED_BACKENDS[PLATFORM][0](full_path, deny_user, deny_group)


def unprotect_path(full_path: str, entry: FileEntry) -> None:
    """Reverse whatever protect_path()/protect_path_scoped() applied.

    Dispatches on the recorded entry's scope (section 29.2): a non-"all"
    scope was necessarily created by protect_path_scoped(), so it must be
    reversed by the matching scoped backend, not the blanket one.
    """
    scope = entry.get("scope", "all")
    if scope != "all" and PLATFORM in SCOPED_BACKENDS:
        SCOPED_BACKENDS[PLATFORM][1](full_path, entry)
        return
    BACKENDS[PLATFORM][1](full_path, entry)


def query_path(full_path: str, entry: FileEntry | None = None) -> tuple[str, str | None]:
    """Inspect the real, current OS-level protection state of full_path.

    When `entry` is given and its scope is not "all", queries the specific
    deny-user/deny-group ACL entries recorded for it instead of the
    blanket-protection state (section 29.2: status/doctor "inspects real
    ACL/ACE state ... never trusts the JSON alone" - the entry only tells
    us *which* identities to check for, not whether they are still denied).
    """
    if entry is not None:
        scope = entry.get("scope", "all")
        if scope != "all" and PLATFORM in SCOPED_BACKENDS:
            return SCOPED_BACKENDS[PLATFORM][2](
                full_path, scope_deny_user(scope), scope_deny_group(scope)
            )
    return BACKENDS[PLATFORM][2](full_path)
