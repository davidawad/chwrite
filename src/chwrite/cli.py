"""init/add/remove/apply/lock/unlock/unlocked subcommands + argparse wiring
(SPEC.md 21, 24, 25, 28, 29). status/verify/doctor live in diagnostics.py -
see that module's docstring for why. install/uninstall live in the separate
chwrite-setup binary (setup_cli.py) - see SPEC.md section 32 for why."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass

from chwrite.backends import protect_path, protect_path_scoped, unprotect_path
from chwrite.claude_hook import cmd_check_path
from chwrite.diagnostics import cmd_doctor, cmd_status, cmd_verify
from chwrite.errors import ChwriteError
from chwrite.gitutil import (
    check_symlink_safety,
    normalize_local_arg_path,
    repo_root,
    validate_pathspec,
)
from chwrite.policy import (
    ADHOC_DEFAULT_MESSAGE,
    POLICY_FILENAME_JSON,
    POLICY_FILENAME_PLAIN,
    POLICY_FILENAME_TOML,
    POLICY_FILENAME_YAML,
    POLICY_WRITERS,
    Rule,
    find_policy_file,
    load_policy,
)
from chwrite.reconcile import reconcile
from chwrite.state import (
    FileEntry,
    StateDoc,
    determine_original_mode,
    load_state,
    make_scope,
    save_state,
)

# The dataclasses below give each cmd_*() function's body real static
# typing for its arguments. At runtime argparse hands every command a
# plain argparse.Namespace (see main()); its attributes match these
# fields structurally (argparse sets them by the `dest` name), so this is
# the standard "lie to the type checker at the untyped boundary" idiom
# for making the rest of an argparse-based CLI checkable under strict
# mode without a third-party typed-argparse dependency.


@dataclass
class _InitArgs:
    format: str


@dataclass
class _AddArgs:
    pathspec: str
    message: str | None


@dataclass
class _RemoveArgs:
    pathspec: str


@dataclass
class _ApplyArgs:
    quiet: bool


@dataclass
class _LockArgs:
    path: str | None
    hard: bool
    message: str | None
    deny_user: str | None
    deny_group: str | None


@dataclass
class _UnlockArgs:
    path: str | None
    all: bool


@dataclass
class _UnlockedArgs:
    command: list[str]


def _split_names(raw: str | None) -> list[str]:
    """Parse a comma-separated --deny-user/--deny-group CLI value."""
    if not raw:
        return []
    return [n.strip() for n in raw.split(",") if n.strip()]


def cmd_init(args: _InitArgs) -> int:
    """Create a new, empty policy file (SPEC.md section 20)."""
    root = repo_root()
    existing = find_policy_file(root)
    if existing:
        raise ChwriteError(f"a chwrite policy file already exists: {existing}", 2)
    filename = {
        "plain": POLICY_FILENAME_PLAIN,
        "json": POLICY_FILENAME_JSON,
        "toml": POLICY_FILENAME_TOML,
        "yaml": POLICY_FILENAME_YAML,
    }[args.format]
    path = os.path.join(root, filename)
    POLICY_WRITERS[filename](path, 1, [])
    print(f"created {filename}")
    return 0


def cmd_add(args: _AddArgs) -> int:
    """Add (or update) a protect rule in the existing policy file."""
    root = repo_root()
    filename = find_policy_file(root)
    if filename is None:
        raise ChwriteError("no chwrite policy file found; run 'chwrite init' first", 2)
    policy = load_policy(root)
    assert policy is not None  # find_policy_file already confirmed one exists
    validate_pathspec(args.pathspec, root)
    rules = list(policy.rules)
    for idx, r in enumerate(rules):
        if r.pattern == args.pathspec:
            rules[idx] = Rule(
                args.pathspec,
                args.message if args.message is not None else r.message,
                r.regex,
                r.deny_user,
                r.deny_group,
            )
            break
    else:
        rules.append(Rule(args.pathspec, args.message))
    POLICY_WRITERS[filename](policy.path, policy.version, rules)
    suffix = f' message="{args.message}"' if args.message else ""
    print(f"protect {args.pathspec}{suffix}  ({filename})")
    return 0


def cmd_remove(args: _RemoveArgs) -> int:
    """Remove a protect rule from the existing policy file."""
    root = repo_root()
    filename = find_policy_file(root)
    if filename is None:
        raise ChwriteError("no chwrite policy file found; run 'chwrite init' first", 2)
    policy = load_policy(root)
    assert policy is not None
    rules = [r for r in policy.rules if r.pattern != args.pathspec]
    if len(rules) == len(policy.rules):
        sys.stderr.write(f"no rule found for pathspec: {args.pathspec}\n")
        return 1
    POLICY_WRITERS[filename](policy.path, policy.version, rules)
    print(f"removed {args.pathspec}  ({filename})")
    return 0


def cmd_apply(args: _ApplyArgs) -> int:
    """(Re)apply protection according to the policy file. Idempotent."""
    root = repo_root()
    state = load_state(root)
    _, report = reconcile(root, state, hard_all=False)
    save_state(root, state)
    if not args.quiet:
        if not report:
            print("chwrite apply: nothing to do (already up to date)")
        else:
            for event in report:
                if event.kind in ("locked", "relocked"):
                    print(f"{event.kind} {event.rel}  [{event.level}]")
                elif event.kind == "removed":
                    print(f"unprotected {event.rel}  (no longer in policy)")
    return 0


def _cmd_lock_single(args: _LockArgs, root: str, state: StateDoc) -> int:
    assert args.path is not None
    rel = normalize_local_arg_path(args.path, root)
    full = os.path.join(root, rel)
    if not os.path.exists(full):
        raise ChwriteError(f"no such file: {args.path}", 2)
    if not check_symlink_safety(full, root):
        raise ChwriteError(f"refusing to protect symlink pointing outside repo root: {rel}", 2)
    entry = state["files"].get(rel)
    original_mode = determine_original_mode(full, entry)
    message = args.message
    if message is None and entry and entry.get("source") == "adhoc":
        message = entry.get("message")
    if message is None:
        message = ADHOC_DEFAULT_MESSAGE

    deny_user = _split_names(args.deny_user)
    deny_group = _split_names(args.deny_group)
    scope = make_scope(deny_user, deny_group)
    if scope != "all":
        if args.hard:
            raise ChwriteError(
                "--hard cannot be combined with --deny-user/--deny-group scoped locks "
                "(scoped ACL-deny backends only offer ENFORCED, never HARD - SPEC.md section 29)",
                2,
            )
        result = protect_path_scoped(full, deny_user, deny_group)
    else:
        result = protect_path(full, hard=args.hard)

    new_entry: FileEntry = {
        "backend": result["backend"],
        "level": result["level"],
        "original_mode": original_mode,
        "locked": True,
        "source": "adhoc",
        "message": message,
        "hard": result.get("hard", False),
        "scope": scope,
    }
    if "acl_user" in result:
        new_entry["acl_user"] = result["acl_user"]
    if "acl_entries" in result:
        new_entry["acl_entries"] = result["acl_entries"]
    state["files"][rel] = new_entry
    save_state(root, state)
    print(f"locked {rel}  [{result['level']}]")
    if args.hard and result["level"] != "HARD":
        return 1
    return 0


def cmd_lock(args: _LockArgs) -> int:
    """Lock all protected files, or ad hoc lock a single path (24.4, 29)."""
    root = repo_root()
    state = load_state(root)

    if args.path:
        return _cmd_lock_single(args, root, state)

    if args.deny_user or args.deny_group:
        raise ChwriteError(
            "--deny-user/--deny-group require a <path> (ad hoc lock); scope for policy-driven "
            "files comes from the policy file's own deny-user=/deny-group= rules instead "
            "(SPEC.md section 29)",
            2,
        )

    _, report = reconcile(root, state, hard_all=args.hard)
    save_state(root, state)
    exit_code = 0
    if not report:
        print("nothing to do (already up to date)")
    for event in report:
        if event.kind in ("locked", "relocked"):
            print(f"{event.kind} {event.rel}  [{event.level}]")
            if args.hard and event.level != "HARD":
                exit_code = 1
        elif event.kind == "removed":
            print(f"unprotected {event.rel}  (no longer in policy)")
    return exit_code


def cmd_unlock(args: _UnlockArgs) -> int:
    """Unlock a single protected file, or every protected file (--all)."""
    root = repo_root()
    state = load_state(root)
    files = state["files"]

    if args.all:
        count = 0
        for rel, entry in files.items():
            if entry.get("locked"):
                full = os.path.join(root, rel)
                if os.path.exists(full):
                    unprotect_path(full, entry)
                entry["locked"] = False
                count += 1
        save_state(root, state)
        print(f"unlocked {count} file(s)")
        return 0

    if not args.path:
        raise ChwriteError("unlock requires a <path> or --all", 2)
    rel = normalize_local_arg_path(args.path, root)
    entry = files.get(rel)
    if not entry or not entry.get("locked"):
        print(f"{rel} is not currently locked")
        return 0
    full = os.path.join(root, rel)
    if os.path.exists(full):
        unprotect_path(full, entry)
    entry["locked"] = False
    save_state(root, state)
    print(f"unlocked {rel}")
    return 0


def _reprotect_entry(full: str, entry: FileEntry) -> None:
    """Reapply whatever protect_path()/protect_path_scoped() previously
    applied to `entry`, using its own recorded scope (SPEC.md 16, 29).
    Mutates `entry` in place with the (possibly refreshed) backend/level.
    """
    scope = entry.get("scope", "all")
    if scope != "all":
        result = protect_path_scoped(full, scope.get("deny_user", []), scope.get("deny_group", []))
    else:
        result = protect_path(full, hard=entry.get("hard", False))
    entry["backend"] = result["backend"]
    entry["level"] = result["level"]
    if "acl_user" in result:
        entry["acl_user"] = result["acl_user"]
    if "acl_entries" in result:
        entry["acl_entries"] = result["acl_entries"]


def cmd_unlocked(args: _UnlockedArgs) -> int:
    """Unlock, run a command, then reapply protection (SPEC.md section 16)."""
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ChwriteError("usage: chwrite unlocked -- <command> [args...]", 2)

    root = repo_root()
    state = load_state(root)
    files = state["files"]
    locked_paths = [rel for rel, e in files.items() if e.get("locked")]
    for rel in locked_paths:
        entry = files[rel]
        full = os.path.join(root, rel)
        if os.path.exists(full):
            unprotect_path(full, entry)
        entry["locked"] = False
    save_state(root, state)

    try:
        proc = subprocess.run(command, check=False)
        code = proc.returncode
    finally:
        state = load_state(root)
        files = state["files"]
        for rel in locked_paths:
            entry = files.get(rel)
            if entry is None:
                continue
            full = os.path.join(root, rel)
            if os.path.exists(full):
                if check_symlink_safety(full, root):
                    _reprotect_entry(full, entry)
                else:
                    sys.stderr.write(
                        f"warning: refusing to reprotect symlink outside repo root: {rel}\n"
                    )
            entry["locked"] = True
        save_state(root, state)

    return code


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser and all subcommands."""
    p = argparse.ArgumentParser(
        prog="chwrite", description="Protect files in a Git repo from unwanted modification."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create a chwrite policy file")
    sp.add_argument("--format", choices=["plain", "json", "toml", "yaml"], default="plain")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add", help="add a protect rule to the policy file")
    sp.add_argument("pathspec")
    sp.add_argument("--message", default=None)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="remove a protect rule from the policy file")
    sp.add_argument("pathspec")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser(
        "apply", help="(re)apply protection according to the policy file (idempotent)"
    )
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("lock", help="lock all protected files, or ad hoc lock a single path")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--hard", action="store_true")
    sp.add_argument("--message", default=None)
    sp.add_argument(
        "--deny-user",
        default=None,
        help="ad hoc scoped lock: deny only this user (comma-separated)",
    )
    sp.add_argument(
        "--deny-group",
        default=None,
        help="ad hoc scoped lock: deny only this group (comma-separated)",
    )
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("unlock", help="unlock a protected file")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_unlock)

    sp = sub.add_parser(
        "unlocked", help="unlock, run a command, then reapply protection regardless of exit status"
    )
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_unlocked)

    sp = sub.add_parser("status", help="show current protection state (inspects real OS state)")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser(
        "verify", help="check protected files for modification/deletion/flag removal"
    )
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("check-path", help="fast check whether a path is protected")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument(
        "--claude-hook",
        action="store_true",
        help="read a Claude Code PreToolUse payload from stdin",
    )
    sp.set_defaults(func=cmd_check_path)

    sp = sub.add_parser("doctor", help="diagnose chwrite installation/backend health")
    sp.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: parse argv, dispatch, and map errors to exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    try:
        return int(func(args))
    except ChwriteError as e:
        sys.stderr.write(f"chwrite: {e}\n")
        return e.code
    except KeyboardInterrupt:
        return 130
