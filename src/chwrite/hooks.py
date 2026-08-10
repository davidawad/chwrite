"""Git hook dispatcher install/uninstall (SPEC.md sections 6-8)."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess

from chwrite.backends import PLATFORM, unprotect_path
from chwrite.claude_hook import install_claude_hook
from chwrite.errors import ChwriteError
from chwrite.gitutil import repo_root
from chwrite.state import load_state, state_paths

HOOK_NAMES = ["post-checkout", "post-merge", "post-rewrite", "pre-commit", "pre-push"]


def config_dir() -> str:
    """Per-user chwrite install directory (SPEC.md section 6)."""
    if PLATFORM == "windows":
        base = os.environ.get("APPDATA")
        if not base:
            raise ChwriteError("%APPDATA% is not set", 2)
        return os.path.join(base, "chwrite")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "chwrite")


def _hook_script(name: str, chwrite_py_path: str) -> str:
    # Git always executes hooks through a shell (on Windows, via the
    # sh.exe bundled with Git for Windows), so a single POSIX-style
    # shebang script works unmodified on macOS, Linux, and Windows - no
    # separate .cmd hook is needed.
    sub = "verify" if name in ("pre-commit", "pre-push") else "apply --quiet"
    return (
        "#!/bin/sh\n"
        "# Installed by `chwrite install`; safe to delete if you uninstall chwrite.\n"
        f'exec python3 "{chwrite_py_path}" {sub}\n'
    )


def cmd_install(args: argparse.Namespace) -> int:
    """Install chwrite + global git hooks for the current OS user."""
    cfg_dir = config_dir()
    os.makedirs(cfg_dir, exist_ok=True)
    dest_py = os.path.join(cfg_dir, "chwrite.py")
    src_py = os.path.realpath(__file__)
    if os.path.realpath(dest_py) != src_py:
        shutil.copy2(src_py, dest_py)
    os.chmod(dest_py, 0o755)

    hooks_dir = os.path.join(cfg_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    for name in HOOK_NAMES:
        hook_path = os.path.join(hooks_dir, name)
        with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(_hook_script(name, dest_py))
        os.chmod(hook_path, 0o755)

    proc = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"], capture_output=True, check=False
    )
    current = proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
    current_real = os.path.realpath(current) if current else None
    if current and current_real != os.path.realpath(hooks_dir) and not args.force:
        raise ChwriteError(
            f"core.hooksPath is already set to '{current}'.\n"
            f"chwrite will not silently overwrite it. Re-run 'chwrite install --force' to point it "
            f"at {hooks_dir} instead, or configure it manually.",
            2,
        )
    subprocess.run(["git", "config", "--global", "core.hooksPath", hooks_dir], check=True)

    print(f"installed chwrite to {dest_py}")
    print(f"installed global git hooks to {hooks_dir}")
    print("core.hooksPath configured")

    if args.claude_hook:
        install_claude_hook(os.getcwd())
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    """Remove the global chwrite install/hooks and unlock the current repo."""
    cfg_dir = config_dir()
    hooks_dir = os.path.join(cfg_dir, "hooks")

    proc = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"], capture_output=True, check=False
    )
    current = proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
    if current and os.path.realpath(current) == os.path.realpath(hooks_dir):
        subprocess.run(["git", "config", "--global", "--unset", "core.hooksPath"], check=False)
        print("removed core.hooksPath")
    elif current:
        print(f"core.hooksPath points elsewhere ({current}); leaving it alone")

    try:
        root = repo_root()
    except ChwriteError:
        root = None
    if root:
        state = load_state(root)
        files = state["files"]
        count = 0
        for _rel, entry in files.items():
            if entry.get("locked"):
                full = os.path.join(root, _rel)
                if os.path.exists(full):
                    unprotect_path(full, entry)
                count += 1
        if count:
            print(f"unlocked {count} file(s) in {root}")
        state_dir, _ = state_paths(root)
        if os.path.isdir(state_dir):
            shutil.rmtree(state_dir)

    if os.path.isdir(cfg_dir):
        shutil.rmtree(cfg_dir)
        print(f"removed {cfg_dir}")
    return 0
