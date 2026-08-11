"""status / verify / doctor subcommands (SPEC.md 15, 17, 21, 29.1).

Split out of cli.py to stay under the swe Python plugin pack's 600-line
source-file ceiling (SPEC.md section 26) - these three commands share the
Linux deny-group caveat helpers and none of init/add/remove/apply/lock/
unlock's bookkeeping, so they're a natural, low-coupling module boundary.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

from chwrite.backends import PLATFORM, query_path
from chwrite.backends.linux import linux_acl_capability
from chwrite.config_paths import config_dir
from chwrite.errors import ChwriteError
from chwrite.gitutil import repo_root, run_git
from chwrite.policy import find_policy_file, load_policy
from chwrite.state import StateDoc, load_state

LINUX_DENY_GROUP_CAVEAT = (
    "NOTE: deny-group is best-effort on Linux - POSIX ACL group entries are additive, so this "
    "only reliably blocks the target group if the affected user has no other group membership "
    "granting the same access (SPEC.md section 29.1). Affected files:"
)


@dataclass
class _VerifyArgs:
    quiet: bool


def _linux_deny_group_files(state: StateDoc) -> list[str]:
    """Files with an active deny-group scope on Linux (SPEC.md 29.1, 29.2)."""
    if PLATFORM != "linux":
        return []
    out: list[str] = []
    for rel, entry in sorted(state["files"].items()):
        if not entry.get("locked"):
            continue
        scope = entry.get("scope", "all")
        if isinstance(scope, dict) and scope.get("deny_group"):
            out.append(rel)
    return out


def _print_deny_group_caveat(state: StateDoc) -> None:
    """Emit the section 29.1 caveat whenever a deny-group scope is active on
    Linux - every invocation, not just once (shared by status/doctor)."""
    group_caveat_files = _linux_deny_group_files(state)
    if group_caveat_files:
        print()
        print(LINUX_DENY_GROUP_CAVEAT)
        for rel in group_caveat_files:
            print(f"  {rel}")


def cmd_status(_args: argparse.Namespace) -> int:
    """Show current protection state, inspecting real OS state (section 9)."""
    root = repo_root()
    policy = load_policy(root)
    state = load_state(root)
    files = state["files"]

    print("chwrite v1")
    print()
    print(f"Policy: {policy.path if policy else '(none)'}")
    print()

    rows: list[tuple[str, str, str]] = []
    protected_count = 0
    violation_count = 0
    for rel in sorted(files.keys()):
        entry = files[rel]
        if not entry.get("locked"):
            continue
        full = os.path.join(root, rel)
        actual_level, actual_backend = query_path(full, entry)
        if actual_level in ("MISSING", "UNPROTECTED"):
            violation_count += 1
        else:
            protected_count += 1
        rows.append((actual_level, actual_backend or entry.get("backend", "-"), rel))

    if rows:
        print(f"{'LEVEL':<10} {'BACKEND':<15} FILE")
        for level, backend, rel in rows:
            print(f"{level:<10} {backend:<15} {rel}")
        print()

    print(f"{protected_count} protected")
    print(f"{violation_count} violations")

    _print_deny_group_caveat(state)

    return 1 if violation_count else 0


def cmd_verify(args: _VerifyArgs) -> int:
    """Check protected files for modification/deletion/flag removal (17)."""
    root = repo_root()
    state = load_state(root)
    files = state["files"]
    violations: list[tuple[str, str]] = []

    for rel, entry in sorted(files.items()):
        if not entry.get("locked"):
            continue
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            violations.append(("D", rel))
            continue
        proc = run_git(["status", "--porcelain", "-z", "--", rel], cwd=root, check=False)
        raw = proc.stdout.decode("utf-8", errors="surrogateescape")
        for item in filter(None, raw.split("\x00")):
            if len(item) < 3:
                continue
            code = item[:2].strip() or "?"
            path = item[3:]
            violations.append((code, path))
        actual_level, _ = query_path(full, entry)
        if actual_level == "UNPROTECTED":
            violations.append(("FLAGS-REMOVED", rel))

    if violations:
        sys.stderr.write("ERROR: chwrite violation\n\n")
        for code, path in violations:
            sys.stderr.write(f"{code} {path}\n")
        sys.stderr.write("\nRun:\n\n    chwrite status\n")
        return 1

    if not args.quiet:
        print("chwrite verify: OK")
    return 0


def _doctor_print_tools() -> None:
    tool_map = {
        "macos": ["chflags", "chmod"],
        "linux": ["chattr", "lsattr"],
        "windows": ["icacls"],
        "posix": ["chmod"],
    }
    for tool in tool_map.get(PLATFORM, []):
        found = shutil.which(tool)
        print(f"  {tool}: {'found at ' + found if found else 'NOT FOUND'}")
    if PLATFORM == "linux":
        print(f"  ACL support (deny-user/deny-group): {linux_acl_capability()}")


def _doctor_print_hooks_path(cfg_dir: str) -> None:
    proc = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"], capture_output=True, check=False
    )
    hooks_path = proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
    expected = os.path.join(cfg_dir, "hooks")
    if hooks_path:
        matches = os.path.realpath(hooks_path) == os.path.realpath(expected)
        suffix = " (matches chwrite install)" if matches else " (points elsewhere)"
        print(f"core.hooksPath: {hooks_path}{suffix}")
    else:
        print("core.hooksPath: not set")


def _doctor_print_privilege_note() -> None:
    if PLATFORM == "linux":
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        privilege_note = (
            "root (HARD protection available)"
            if is_root
            else "unprivileged (chattr +i will require sudo)"
        )
        print(f"privilege: {privilege_note}")
    elif PLATFORM == "macos":
        print("privilege: uchg (ENFORCED) requires no elevation for files you own;")
        print("           schg is not implemented")
    elif PLATFORM == "windows":
        print("privilege: icacls deny ACE (ENFORCED) requires no elevation for files you own")


def _doctor_print_repo_section() -> None:
    try:
        root = repo_root()
        print()
        print(f"Repository: {root}")
        filename = find_policy_file(root)
        print(f"Policy file: {filename if filename else '(none)'}")
        _print_deny_group_caveat(load_state(root))
    except ChwriteError as e:
        print()
        print(f"Not currently inside a git repository ({e})")


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Diagnose OS/backend/install/hook health (SPEC.md section 21, 29.1)."""
    print("chwrite doctor")
    print()
    print(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Detected backend: {PLATFORM}")
    _doctor_print_tools()

    git_path = shutil.which("git")
    if git_path:
        proc = subprocess.run(["git", "--version"], capture_output=True, check=False)
        print(f"git: {proc.stdout.decode(errors='replace').strip()} ({git_path})")
    else:
        print("git: NOT FOUND (required)")

    cfg_dir = config_dir()
    dest_py = os.path.join(cfg_dir, "chwrite.py")
    install_note = dest_py if os.path.isfile(dest_py) else "not installed (run chwrite install)"
    print(f"global install: {install_note}")

    _doctor_print_hooks_path(cfg_dir)
    _doctor_print_privilege_note()
    _doctor_print_repo_section()
    return 0
