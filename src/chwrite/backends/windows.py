"""Windows backend: icacls.exe explicit deny ACE (SPEC.md section 12),
generalized to an arbitrary named user/group for scoped locks (section 29)."""

from __future__ import annotations

import contextlib
import getpass
import os
import shutil
import stat
import subprocess
import sys

from chwrite.errors import ChwriteError
from chwrite.state import FileEntry, ProtectResult


def _windows_username() -> str:
    return os.environ.get("USERNAME") or getpass.getuser()


def _icacls_path() -> str | None:
    return shutil.which("icacls") or shutil.which("icacls.exe")


def protect_windows(full_path: str, hard: bool) -> ProtectResult:
    """Apply an icacls deny ACE (ENFORCED), or FILE_ATTRIBUTE_READONLY."""
    if hard:
        sys.stderr.write(
            "note: HARD protection is not available on Windows (see SPEC.md section 12); "
            "applying ENFORCED (icacls deny ACE) instead.\n"
        )
    icacls = _icacls_path()
    if icacls:
        user = _windows_username()
        # We remove exactly the deny ACE we add (icacls /remove:d) on
        # unlock rather than snapshot/restoring the whole DACL via
        # `icacls /save`/`/restore`: that round-trip uses a binary format
        # we cannot validate without a Windows machine to test against,
        # and a naive restore risks clobbering ACL entries another tool
        # added in the meantime. Removing only our own explicit deny
        # entry is safer and simpler, at the cost of not being a
        # byte-for-byte ACL restore.
        #
        # Granular rights (WD,AD,WEA,WA), NOT the simple "(W)" alias:
        # confirmed on real Windows CI that `/deny user:(W)` denies READS
        # too, not just writes - icacls's simple "W" write alias silently
        # includes DELETE, and denying DELETE alone (independent of any
        # read-related bit) blocks ordinary file reads on Windows for
        # reasons that don't reduce to documented NTFS generic-rights
        # mappings. (WD,AD,WEA,WA) - write data/append data/write
        # extended attributes/write attributes - was verified bit-by-bit
        # to block write+append while leaving reads unaffected. Omitting
        # DELETE here doesn't weaken the documented guarantee: section 12
        # already says Windows can't reliably block delete/rename via a
        # file-level ACE alone, since the parent directory's
        # FILE_DELETE_CHILD right can permit it regardless.
        proc = subprocess.run(
            [icacls, full_path, "/deny", f"{user}:(WD,AD,WEA,WA)"], capture_output=True, check=False
        )
        if proc.returncode == 0:
            return {
                "backend": "windows-acl",
                "level": "ENFORCED",
                "hard": False,
                "acl_user": user,
            }
    with contextlib.suppress(OSError):
        os.chmod(full_path, stat.S_IREAD)
    return {"backend": "windows-readonly", "level": "READONLY", "hard": False}


def unprotect_windows(full_path: str, entry: FileEntry) -> None:
    """Remove the deny ACE we added, or restore the recorded mode."""
    if entry.get("backend") == "windows-acl":
        icacls = _icacls_path()
        user = entry.get("acl_user") or _windows_username()
        if icacls:
            subprocess.run([icacls, full_path, "/remove:d", user], capture_output=True, check=False)
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        with contextlib.suppress(OSError):
            os.chmod(full_path, original_mode)


def query_windows(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state - never trust state.json alone.

    Best-effort: icacls output is localized on non-English Windows, so the
    DENY substring check is not fully robust. It is only used to detect
    flag *removal* for status/verify; the authoritative record of what
    chwrite applied lives in state.json.
    """
    try:
        st = os.stat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    icacls = _icacls_path()
    if icacls:
        proc = subprocess.run([icacls, full_path], capture_output=True, check=False)
        if proc.returncode == 0 and "DENY" in proc.stdout.decode(errors="replace").upper():
            return "ENFORCED", "windows-acl"
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None and attrs & 0x1:  # FILE_ATTRIBUTE_READONLY
        return "READONLY", "windows-readonly"
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "windows-readonly"
    return "UNPROTECTED", None


# --- Scoped (deny-user/deny-group) backend: SPEC.md section 29 ------------
#
# icacls already supports an explicit deny ACE (protect_windows() above);
# this generalizes the target from "current user's SID" to an arbitrary
# named user or group's SID. icacls's `/deny "<name>:(W)"` syntax accepts
# either a user or a group name identically, so one implementation serves
# both. Classification stays ENFORCED (the object owner can still edit its
# own DACL - same caveat as section 12).


def protect_windows_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply one icacls deny ACE per named user/group. Never falls back to
    FILE_ATTRIBUTE_READONLY (that would misrepresent who is denied) - if
    icacls is unavailable, or a name can't be resolved to a SID, this
    raises rather than silently doing something weaker (SPEC.md 29.1)."""
    icacls = _icacls_path()
    if not icacls:
        raise ChwriteError(
            "deny-user/deny-group scoped locks require icacls.exe on Windows, which was not "
            "found on PATH; a scoped ACL deny cannot be applied (SPEC.md section 29)",
            2,
        )
    applied: list[str] = []
    for name in [*deny_user, *deny_group]:
        # (WD,AD,WEA,WA), not the simple "(W)" alias - see protect_windows()
        # above for why: "(W)" silently includes DELETE, and denying
        # DELETE alone blocks ordinary file reads on Windows.
        proc = subprocess.run(
            [icacls, full_path, "/deny", f"{name}:(WD,AD,WEA,WA)"], capture_output=True, check=False
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"icacls failed to deny write access to {name!r} on {full_path}: {stderr} "
                f"(chwrite never creates users/groups - {name!r} must already resolve to a SID)",
                2,
            )
        applied.append(name)
    return {
        "backend": "windows-acl-deny",
        "level": "ENFORCED",
        "hard": False,
        "acl_entries": applied,
    }


def unprotect_windows_scoped(full_path: str, entry: FileEntry) -> None:
    """Remove exactly the deny ACEs protect_windows_scoped() added."""
    icacls = _icacls_path()
    if not icacls or not os.path.exists(full_path):
        return
    for name in entry.get("acl_entries", []):
        subprocess.run([icacls, full_path, "/remove:d", name], capture_output=True, check=False)


def query_windows_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> tuple[str, str | None]:
    """Inspect the real ACL (`icacls`) for the specific deny entries requested."""
    try:
        os.stat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    names = [*deny_user, *deny_group]
    if not names:
        return "UNPROTECTED", None
    icacls = _icacls_path()
    if not icacls:
        return "UNPROTECTED", None
    proc = subprocess.run([icacls, full_path], capture_output=True, check=False)
    if proc.returncode != 0:
        return "UNPROTECTED", None
    out = proc.stdout.decode(errors="replace").upper()
    if all(f"{name.upper()}:(DENY)" in out for name in names):
        return "ENFORCED", "windows-acl-deny"
    return "UNPROTECTED", None
