"""macOS backend: BSD file flags (SPEC.md section 10) and NFSv4-style ACL
deny entries for scoped locks (SPEC.md section 29).

PATH-shadowing note (verified, not just assumed): `chflags` below is
resolved via plain `shutil.which("chflags")`/PATH lookup, unlike the ACL
`chmod`/`ls` calls further down which pin `/bin/chmod`/`/bin/ls`
explicitly. This is safe because `chflags` is a BSD-only utility with no
GNU coreutils equivalent at all (`which -a chflags` on a machine with GNU
coreutils earlier on PATH still resolves to a single `/usr/bin/chflags` -
there is nothing to shadow it with, unlike `chmod`/`ls` which coreutils
does ship and which silently reject BSD-only flags like `+a`/`-e` instead
of erroring in a way that would out itself)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

from chwrite.backends.posix_generic import chmod_readonly
from chwrite.errors import ChwriteError
from chwrite.state import FileEntry, ProtectResult


def protect_macos(full_path: str, hard: bool) -> ProtectResult:
    """Apply chflags uchg (ENFORCED), falling back to chmod (READONLY)."""
    if hard:
        sys.stderr.write(
            "note: HARD protection (schg) is not implemented on macOS by chwrite "
            "(see SPEC.md section 10); applying ENFORCED (uchg) instead.\n"
        )
    if shutil.which("chflags"):
        proc = subprocess.run(["chflags", "uchg", full_path], capture_output=True, check=False)
        if proc.returncode == 0:
            return {"backend": "macos-uchg", "level": "ENFORCED", "hard": False}
    chmod_readonly(full_path)
    return {"backend": "macos-chmod", "level": "READONLY", "hard": False}


def unprotect_macos(full_path: str, entry: FileEntry) -> None:
    """Reverse protect_macos, restoring the recorded original mode."""
    if entry.get("backend") == "macos-uchg" and shutil.which("chflags"):
        # nouchg must run before chmod: the BSD immutable flag also blocks
        # ordinary chmod() calls while set.
        subprocess.run(["chflags", "nouchg", full_path], capture_output=True, check=False)
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        os.chmod(full_path, original_mode)


def query_macos(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state - never trust state.json alone."""
    try:
        st = os.lstat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    uf_immutable = getattr(stat, "UF_IMMUTABLE", 0x00000002)
    flags = getattr(st, "st_flags", 0)
    if flags & uf_immutable:
        return "ENFORCED", "macos-uchg"
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "macos-chmod"
    return "UNPROTECTED", None


# --- Scoped (deny-user/deny-group) backend: SPEC.md section 29 ------------
#
# APFS/HFS+ support NFSv4-style ACL entries via `chmod +a`/`chmod -a`,
# which - unlike the blanket `uchg` flag above - support an explicit
# *deny* clause for a named user or group. This is a genuinely different
# mechanism from protect_macos()'s file-flag approach, selected only when
# a rule/lock carries a deny-user=/deny-group= scope. Classification is
# ENFORCED (the file owner can still edit its own ACL - same caveat as
# the blanket uchg backend and as Windows' icacls deny ACE).

_DENY_RIGHTS = "deny write,delete,append,writeattr,chown"


def _macos_ace(kind: str, name: str) -> str:
    return f"{kind}:{name} {_DENY_RIGHTS}"


def _macos_chmod_path() -> str | None:
    """Prefer the system `/bin/chmod` over whatever `chmod` resolves to on
    PATH. macOS's own chmod is the only one that understands `+a`/`-a`
    NFSv4-style ACL syntax; a GNU coreutils `chmod` earlier on PATH (common
    with Homebrew's `coreutils` formula, or this project's own Nix dev
    environment) silently rejects `+a` as "invalid mode" instead - falling
    back to shutil.which("chmod") here would risk shelling out to the
    wrong binary entirely, which is exactly the kind of silent-wrong-thing
    SPEC.md 29.1 says never to do."""
    if os.path.exists("/bin/chmod"):
        return "/bin/chmod"
    return shutil.which("chmod")


def protect_macos_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply one `chmod +a` deny ACE per named user/group. Never falls back:
    if this fails, the caller learns exactly what's missing (SPEC.md 29.1's
    "never silently fall back to blanket-block or silently no-op")."""
    chmod = _macos_chmod_path()
    if not chmod:
        raise ChwriteError("chmod not found; cannot apply a macOS ACL deny entry", 2)
    applied: list[str] = []
    for name in deny_user:
        ace = _macos_ace("user", name)
        proc = subprocess.run([chmod, "+a", ace, full_path], capture_output=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"chmod +a failed to deny write access to user {name!r} on {full_path}: {stderr}",
                2,
            )
        applied.append(ace)
    for name in deny_group:
        ace = _macos_ace("group", name)
        proc = subprocess.run([chmod, "+a", ace, full_path], capture_output=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"chmod +a failed to deny write access to group {name!r} on {full_path}: "
                f"{stderr} (chwrite never creates/modifies groups - {name!r} must already exist)",
                2,
            )
        applied.append(ace)
    return {"backend": "macos-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": applied}


def unprotect_macos_scoped(full_path: str, entry: FileEntry) -> None:
    """Remove exactly the ACEs protect_macos_scoped() added, in reverse order."""
    chmod = _macos_chmod_path()
    if not chmod or not os.path.exists(full_path):
        return
    for ace in reversed(entry.get("acl_entries", [])):
        subprocess.run([chmod, "-a", ace, full_path], capture_output=True, check=False)


def _macos_ls_path() -> str:
    """Same PATH-shadowing concern as _macos_chmod_path(): GNU coreutils
    `ls` has no `-e` (ACL display) flag at all, so it must be the real
    macOS `/bin/ls`, not whatever `ls` resolves to first on PATH."""
    if os.path.exists("/bin/ls"):
        return "/bin/ls"
    return shutil.which("ls") or "ls"


def query_macos_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> tuple[str, str | None]:
    """Inspect the real ACL (`ls -le`) for the specific deny entries requested -
    never trusts state.json alone, same principle as query_macos()."""
    if not os.path.exists(full_path):
        return "MISSING", None
    if not (deny_user or deny_group):
        return "UNPROTECTED", None
    proc = subprocess.run([_macos_ls_path(), "-le", full_path], capture_output=True, check=False)
    if proc.returncode != 0:
        return "UNPROTECTED", None
    out = proc.stdout.decode(errors="replace")
    for name in deny_user:
        if f"user:{name} deny" not in out:
            return "UNPROTECTED", None
    for name in deny_group:
        if f"group:{name} deny" not in out:
            return "UNPROTECTED", None
    return "ENFORCED", "macos-acl-deny"
