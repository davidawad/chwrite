"""Git hook dispatcher install/uninstall (SPEC.md sections 6-8, 32).

Exclusive to the chwrite-setup binary (SPEC.md section 32.1) - never
bundled into the hot-path chwrite binary, since nothing in here runs on
a normal per-repo invocation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess

from chwrite.backends import unprotect_path
from chwrite.claude_hook import install_claude_hook
from chwrite.config_paths import config_dir
from chwrite.errors import ChwriteError
from chwrite.gitutil import repo_root
from chwrite.state import load_state, state_paths

HOOK_NAMES = ["post-checkout", "post-merge", "post-rewrite", "pre-commit", "pre-push"]


def _hook_script(name: str, chwrite_py_path: str) -> str:
    # Git always executes hooks through a shell (on Windows, via the
    # sh.exe bundled with Git for Windows), so a single POSIX-style
    # shebang script works unmodified on macOS, Linux, and Windows - no
    # separate .cmd hook is needed.
    sub = "verify" if name in ("pre-commit", "pre-push") else "apply --quiet"
    return (
        "#!/bin/sh\n"
        "# Installed by `chwrite-setup install`; safe to delete if you uninstall chwrite.\n"
        f'exec python3 "{chwrite_py_path}" {sub}\n'
    )


def _hook_script_on_path(name: str) -> str:
    """Variant of _hook_script() for when `chwrite` is already a real,
    durable command on PATH (pip/pipx/Homebrew/npm/AUR/apt install,
    SPEC.md section 31) - invoke it directly, no interpreter/file path
    to hardcode, same as any other installed tool's hooks would."""
    sub = "verify" if name in ("pre-commit", "pre-push") else "apply --quiet"
    return (
        "#!/bin/sh\n"
        "# Installed by `chwrite-setup install`; safe to delete if you uninstall chwrite.\n"
        f"exec chwrite {sub}\n"
    )


def _find_chwrite_py() -> tuple[str, bool]:
    """Locate the hot-path chwrite to install hooks against (SPEC.md
    32.2). Returns (path_or_command, is_on_path).

    Preference order: a `chwrite` already on PATH (durable, no copy
    needed - the common case for anyone who installed via a package
    manager, SPEC.md section 31), else a sibling chwrite.py next to
    wherever chwrite-setup.py itself is running from (the curl-one-file
    case, mirroring how the `chwrite`/`chwrite.cmd` launchers resolve
    their own sibling chwrite.py, SPEC.md section 19).
    """
    on_path = shutil.which("chwrite")
    if on_path:
        return on_path, True
    self_dir = os.path.dirname(os.path.realpath(__file__))
    sibling = os.path.join(self_dir, "chwrite.py")
    if os.path.isfile(sibling):
        return sibling, False
    raise ChwriteError(
        "could not find chwrite: no 'chwrite' on PATH and no sibling chwrite.py next to "
        "chwrite-setup. Install chwrite first (pip/pipx/Homebrew/npm/AUR/apt, or download "
        "chwrite.py next to this script), then re-run chwrite-setup install.",
        2,
    )


def cmd_install(args: argparse.Namespace) -> int:
    """Install chwrite's global git hooks for the current OS user."""
    cfg_dir = config_dir()
    os.makedirs(cfg_dir, exist_ok=True)

    chwrite_ref, is_on_path = _find_chwrite_py()
    if is_on_path:
        print(f"using chwrite already on PATH: {chwrite_ref}")
    else:
        dest_py = os.path.join(cfg_dir, "chwrite.py")
        if os.path.realpath(dest_py) != os.path.realpath(chwrite_ref):
            shutil.copy2(chwrite_ref, dest_py)
        os.chmod(dest_py, 0o755)
        chwrite_ref = dest_py
        print(f"installed chwrite to {dest_py}")

    hooks_dir = os.path.join(cfg_dir, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)
    for name in HOOK_NAMES:
        hook_path = os.path.join(hooks_dir, name)
        body = _hook_script_on_path(name) if is_on_path else _hook_script(name, chwrite_ref)
        with open(hook_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        os.chmod(hook_path, 0o755)

    proc = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"], capture_output=True, check=False
    )
    current = proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
    current_real = os.path.realpath(current) if current else None
    if current and current_real != os.path.realpath(hooks_dir) and not args.force:
        raise ChwriteError(
            f"core.hooksPath is already set to '{current}'.\n"
            f"chwrite will not silently overwrite it. Re-run 'chwrite-setup install --force' to "
            f"point it at {hooks_dir} instead, or configure it manually.",
            2,
        )
    subprocess.run(["git", "config", "--global", "core.hooksPath", hooks_dir], check=True)

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
