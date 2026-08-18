"""check-path and the Claude Code PreToolUse hook integration (SPEC.md 25, 28)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from chwrite.errors import ChwriteError
from chwrite.gitutil import (
    branch_matches,
    current_branch,
    pathspec_matches,
    resolve_target_path,
    try_repo_root,
)
from chwrite.policy import (
    ADHOC_DEFAULT_MESSAGE,
    Rule,
    branch_condition_note,
    default_message_for,
    load_policy,
)
from chwrite.state import load_state

CLAUDE_HOOK_MATCHER = "Edit|MultiEdit|Write|NotebookEdit"
CLAUDE_HOOK_COMMAND = "chwrite check-path --claude-hook"


def _rule_matches(rule: Rule, rel: str) -> bool:
    """Whether `rule` (pathspec or regex, section 28) matches `rel`.

    Regex rules never resolve paths themselves - they only re.search()
    against an already-safety-checked repo-relative path, same as
    resolve_policy_files() (section 18/28). An invalid regex here would
    already have been rejected at load_policy() time.
    """
    if rule.pattern is not None:
        return pathspec_matches(rule.pattern, rel)
    assert rule.regex is not None
    return re.search(rule.regex, rel) is not None


def _protection_message(root: str, rel: str) -> str | None:
    """The message to show for rel, or None if it is not protected.

    An ad hoc lock's message wins over a policy rule's (SPEC.md 24.4: "more
    specific, more recently expressed intent"). Among policy rules
    themselves, the *last*-defined matching rule wins (section 28's
    documented precedence), matching resolve_policy_files().

    A rule whose `branches=` condition (section 33) does not match the
    current branch contributes nothing here, same as it contributes
    nothing to resolve_policy_files() - so a file that would otherwise be
    caught by a branch-scoped rule reports as unprotected on a branch
    where that rule isn't active, even before `chwrite apply` has ever run
    on this branch (check-path's whole point is to warn before a would-be
    write, not only after state.json has caught up).
    """
    state = load_state(root)
    policy = load_policy(root)
    entry = state["files"].get(rel)
    rule_message = None
    if policy:
        branch: str | None = None
        branch_resolved = False
        for rule in policy.rules:
            if rule.branches:
                if not branch_resolved:
                    branch = current_branch(root)
                    branch_resolved = True
                if not branch_matches(branch, rule.branches):
                    continue
            if _rule_matches(rule, rel):
                base = rule.message or default_message_for(policy)
                rule_message = base + branch_condition_note(rule, branch)
    if entry and entry.get("locked"):
        return entry.get("message") or rule_message or ADHOC_DEFAULT_MESSAGE
    return rule_message


def cmd_check_path(args: argparse.Namespace) -> int:
    """Fast check whether <path> is protected (SPEC.md section 25)."""
    if args.claude_hook:
        return _claude_hook_main()
    if not args.path:
        raise ChwriteError("check-path requires a <path> argument (or --claude-hook)", 2)
    root = try_repo_root()
    if root is None:
        return 0
    rel = resolve_target_path(args.path, root)
    if rel is None:
        return 0
    message = _protection_message(root, rel)
    if message:
        sys.stderr.write(message + "\n")
        return 1
    return 0


def _claude_hook_main() -> int:
    """Claude Code PreToolUse hook entry point (SPEC.md section 25.1).

    Reads the tool-call payload as JSON on stdin and remaps chwrite's own
    check-path contract (0/1/2) onto Claude Code's hook contract, where
    exit 2 blocks the tool call and its stderr is surfaced to the model.
    """
    try:
        payload: object = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.stderr.write("chwrite: malformed hook payload on stdin (expected JSON)\n")
        return 2
    # Parsing an untyped external JSON payload (the Claude Code hook
    # protocol) without a schema library - same accepted boundary-noise
    # tradeoff documented in policy.py's _parse_structured().
    target: object = None
    if isinstance(payload, dict):
        tool_input: object = payload.get(  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
            "tool_input"
        )
        if isinstance(tool_input, dict):
            target = tool_input.get(  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
                "file_path"
            ) or tool_input.get("notebook_path")  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(target, str) or not target:
        return 0
    root = try_repo_root()
    if root is None:
        return 0
    rel = resolve_target_path(target, root)
    if rel is None:
        return 0
    message = _protection_message(root, rel)
    if message:
        sys.stderr.write(message + "\n")
        return 2
    return 0


def install_claude_hook(repo_dir: str) -> None:
    """Add a project-scoped PreToolUse hook to .claude/settings.json.

    Non-destructive: appends to an existing hooks.PreToolUse list rather
    than overwriting it, and refuses (rather than guessing) if the
    existing file/keys are not in the expected shape.
    """
    settings_path = os.path.join(repo_dir, ".claude", "settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    data: dict[str, object]
    if os.path.isfile(settings_path):
        with open(settings_path, encoding="utf-8") as f:
            raw = f.read()
        try:
            loaded: object = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as e:
            raise ChwriteError(
                f"cannot safely modify {settings_path}: invalid JSON ({e})", 2
            ) from e
        if not isinstance(loaded, dict):
            raise ChwriteError(
                f"cannot safely modify {settings_path}: top level is not an object", 2
            )
        data = loaded  # pyright: ignore[reportUnknownVariableType]
    else:
        data = {}

    hooks: object = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ChwriteError(f"cannot safely modify {settings_path}: 'hooks' is not an object", 2)
    hooks_map: dict[str, object] = hooks  # pyright: ignore[reportUnknownVariableType]
    pretool: object = hooks_map.setdefault("PreToolUse", [])
    if not isinstance(pretool, list):
        raise ChwriteError(
            f"cannot safely modify {settings_path}: 'hooks.PreToolUse' is not an array", 2
        )
    pretool_list: list[object] = pretool  # pyright: ignore[reportUnknownVariableType]

    for block in pretool_list:
        if (
            isinstance(block, dict) and block.get("matcher") == CLAUDE_HOOK_MATCHER  # pyright: ignore[reportUnknownMemberType]
        ):
            for h in block.get("hooks", []):  # pyright: ignore[reportUnknownVariableType,reportUnknownMemberType]
                if (
                    isinstance(h, dict) and h.get("command") == CLAUDE_HOOK_COMMAND  # pyright: ignore[reportUnknownMemberType]
                ):
                    print(f"{settings_path} already has a chwrite hook - leaving it unchanged")
                    return

    pretool_list.append(
        {
            "matcher": CLAUDE_HOOK_MATCHER,
            "hooks": [{"type": "command", "command": CLAUDE_HOOK_COMMAND}],
        }
    )

    tmp = settings_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, settings_path)
    print(f"added chwrite PreToolUse hook to {settings_path} (assumes 'chwrite' is on PATH)")
