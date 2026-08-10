"""Linux backend: chmod (default) / chattr immutable attribute (SPEC.md 11),
plus POSIX ACL deny entries for scoped locks (SPEC.md 29, 29.1)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

from chwrite.backends.posix_generic import chmod_readonly
from chwrite.errors import ChwriteError
from chwrite.state import FileEntry, ProtectResult


def protect_linux(full_path: str, hard: bool) -> ProtectResult:
    """Apply chmod a-w (READONLY) by default, or chattr +i (HARD) via --hard.

    Never invokes sudo: if chattr fails for lack of privilege, prints the
    command the user must run themselves (SPEC.md section 11) and falls
    back to READONLY rather than silently doing nothing.
    """
    if hard:
        if shutil.which("chattr"):
            proc = subprocess.run(["chattr", "+i", full_path], capture_output=True, check=False)
            if proc.returncode == 0:
                return {"backend": "linux-chattr", "level": "HARD", "hard": True}
            stderr = proc.stderr.decode(errors="replace").strip()
            sys.stderr.write(
                "Hard protection requires CAP_LINUX_IMMUTABLE.\n\n"
                "Run:\n\n"
                "    sudo chwrite lock --hard\n\n"
                f"(chattr reported: {stderr})\n"
            )
        else:
            sys.stderr.write(
                "chattr not found; cannot apply HARD protection. Falling back to READONLY.\n"
            )
    chmod_readonly(full_path)
    return {"backend": "linux-chmod", "level": "READONLY", "hard": False}


def unprotect_linux(full_path: str, entry: FileEntry) -> None:
    """Reverse protect_linux. Never invokes sudo to clear chattr +i."""
    if entry.get("backend") == "linux-chattr":
        if shutil.which("chattr"):
            proc = subprocess.run(["chattr", "-i", full_path], capture_output=True, check=False)
            if proc.returncode != 0:
                stderr = proc.stderr.decode(errors="replace").strip()
                raise ChwriteError(
                    f"failed to clear immutable flag on {full_path}: {stderr}\n"
                    f"Run:\n\n    sudo chattr -i {full_path}",
                    2,
                )
        else:
            raise ChwriteError(f"chattr not found; cannot clear immutable flag on {full_path}", 2)
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        os.chmod(full_path, original_mode)


def query_linux(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state via lsattr/mode bits - never trust state.json."""
    try:
        st = os.lstat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    if shutil.which("lsattr"):
        proc = subprocess.run(["lsattr", "-d", full_path], capture_output=True, check=False)
        if proc.returncode == 0:
            out = proc.stdout.decode(errors="replace").strip()
            fields = out.split(None, 1)
            if fields and "i" in fields[0]:
                return "HARD", "linux-chattr"
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "linux-chmod"
    return "UNPROTECTED", None


# --- Scoped (deny-user/deny-group) backend: SPEC.md section 29 ------------
#
# POSIX.1e ACLs (what setfacl/getfacl manage) have NO deny entry type -
# only allow entries plus the base owner/group/other bits. The
# access-check algorithm resolves a non-owner user's access via the FIRST
# matching category (named-user entry beats group entries beats "other"),
# so `setfacl -m u:<name>:0 <file>` reliably blocks that specific
# non-owner user regardless of group/other bits. deny-group is weaker:
# POSIX ACL group entries are ADDITIVE, so if the denied group is one of
# several groups granting that user access, the union still grants write
# (SPEC.md 29.1) - chwrite documents this in status/doctor output
# (see cli.py's LINUX_DENY_GROUP_CAVEAT), it does not pretend otherwise.
#
# chwrite MUST NOT create/modify system users or groups, and must refuse
# (not silently no-op or fall back to blanket-block) if setfacl/getfacl or
# ACL filesystem support are unavailable.


def _acl_tools_available() -> bool:
    return shutil.which("setfacl") is not None and shutil.which("getfacl") is not None


def linux_acl_capability() -> str:
    """Human-readable ACL-tooling availability for `chwrite doctor` (29.1)."""
    if _acl_tools_available():
        return "available (setfacl/getfacl found on PATH)"
    return (
        "NOT available - install the 'acl' package (e.g. `apt install acl` / "
        "`dnf install acl`) to use deny-user/deny-group scoped locks"
    )


def protect_linux_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply one `setfacl -m u:<name>:0`/`g:<name>:0` deny-equivalent entry
    per named user/group. Refuses (never falls back or no-ops) if setfacl/
    getfacl are missing or the filesystem rejects the ACL (no ACL mount
    support, or - for deny_group - a nonexistent group name)."""
    if not _acl_tools_available():
        raise ChwriteError(
            "deny-user/deny-group scoped locks require the 'acl' package (setfacl and getfacl) "
            "on Linux, which was not found on PATH. Install it, or use an unscoped `protect` "
            "rule instead (SPEC.md section 29).",
            2,
        )
    applied: list[str] = []
    for name in deny_user:
        proc = subprocess.run(
            ["setfacl", "-m", f"u:{name}:0", full_path], capture_output=True, check=False
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"setfacl failed to deny user {name!r} on {full_path}: {stderr} "
                "(the filesystem may not be mounted with ACL support - see SPEC.md section 29)",
                2,
            )
        applied.append(f"u:{name}")
    for name in deny_group:
        proc = subprocess.run(
            ["setfacl", "-m", f"g:{name}:0", full_path], capture_output=True, check=False
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"setfacl failed to deny group {name!r} on {full_path}: {stderr} "
                f"(the group {name!r} may not exist - chwrite never creates groups - or the "
                "filesystem may not be mounted with ACL support; see SPEC.md section 29)",
                2,
            )
        applied.append(f"g:{name}")
    return {"backend": "linux-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": applied}


def unprotect_linux_scoped(full_path: str, entry: FileEntry) -> None:
    """Remove exactly the ACL entries protect_linux_scoped() added."""
    if not shutil.which("setfacl") or not os.path.exists(full_path):
        return
    for spec in entry.get("acl_entries", []):
        kind, _, name = spec.partition(":")
        subprocess.run(
            ["setfacl", "-x", f"{kind}:{name}", full_path], capture_output=True, check=False
        )


def _getfacl_output(full_path: str) -> str | None:
    if not shutil.which("getfacl"):
        return None
    proc = subprocess.run(["getfacl", "-p", full_path], capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace")


def query_linux_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> tuple[str, str | None]:
    """Inspect the real ACL (`getfacl`) for the specific deny entries requested."""
    if not os.path.exists(full_path):
        return "MISSING", None
    if not (deny_user or deny_group):
        return "UNPROTECTED", None
    out = _getfacl_output(full_path)
    if out is None:
        return "UNPROTECTED", None
    all_denied = all(f"user:{n}:---" in out for n in deny_user) and all(
        f"group:{n}:---" in out for n in deny_group
    )
    if all_denied:
        return "ENFORCED", "linux-acl-deny"
    return "UNPROTECTED", None
