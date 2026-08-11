"""chwrite-setup: the one-time-per-machine binary (SPEC.md section 32).

Deliberately separate from chwrite's own argparse tree - install/
uninstall are the only two commands here, and neither is ever invoked
by the hot-path chwrite binary or its generated git hooks (those always
call plain `chwrite`, never `chwrite-setup`; see hooks.py).
"""

from __future__ import annotations

import argparse
import sys

from chwrite.errors import ChwriteError
from chwrite.hooks import cmd_install, cmd_uninstall


def build_parser() -> argparse.ArgumentParser:
    """Construct the chwrite-setup argparse parser."""
    p = argparse.ArgumentParser(
        prog="chwrite-setup",
        description="One-time setup: install/uninstall chwrite's global git hooks.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("install", help="install chwrite's global git hooks for this user")
    sp.add_argument("--force", action="store_true", help="overwrite an existing core.hooksPath")
    sp.add_argument(
        "--claude-hook", action="store_true", help="also add a project-scoped Claude Code hook here"
    )
    sp.set_defaults(func=cmd_install)

    sp = sub.add_parser("uninstall", help="remove the global chwrite install and hooks")
    sp.set_defaults(func=cmd_uninstall)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: parse argv, dispatch, and map errors to exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    try:
        return int(func(args))
    except ChwriteError as e:
        sys.stderr.write(f"chwrite-setup: {e}\n")
        return e.code
    except KeyboardInterrupt:
        return 130
