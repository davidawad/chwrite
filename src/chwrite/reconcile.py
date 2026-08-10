"""State reconciliation shared by `apply` and bare `lock` (SPEC.md 7-8, 18, 29)."""

from __future__ import annotations

import os
import sys
from typing import NamedTuple

from chwrite.backends import protect_path, protect_path_scoped, query_path, unprotect_path
from chwrite.gitutil import check_symlink_safety
from chwrite.policy import Policy, load_policy, resolve_policy_files
from chwrite.state import (
    FileEntry,
    StateDoc,
    determine_original_mode,
    make_scope,
    scope_deny_group,
    scope_deny_user,
)


class ReconcileEvent(NamedTuple):
    """One thing reconcile() did, for apply/lock to report to the user."""

    kind: str  # "locked" | "relocked" | "removed"
    rel: str
    level: str | None = None


def reconcile(  # noqa: PLR0912, PLR0915
    root: str, state: StateDoc, hard_all: bool = False
) -> tuple[Policy | None, list[ReconcileEvent]]:
    """Sync OS-level protection to the current policy file + self-heal.

    Branch count intentionally not reduced further: this is the one place
    the three reconciliation phases (drop removed-from-policy entries,
    (re)lock desired policy files, self-heal ad hoc locks) live together
    so `apply`/`lock`'s idempotency and section 23's acceptance test are
    easy to reason about from a single function; splitting it up would
    trade that for indirection without reducing real complexity.

    Also re-protects any currently-locked entry (policy or ad hoc) whose
    OS-level protection has drifted away (e.g. a file replaced by checkout
    or merge, per SPEC.md sections 7-8). Idempotent: a no-op second call
    performs no OS calls and produces an empty report.

    A policy rule's deny-user=/deny-group= scope (section 29) is applied
    via protect_path_scoped() instead of the blanket protect_path(); a
    scope change (including scoped -> blanket or vice versa) is itself
    treated as "needs reapply", same as a level/lock-state drift.
    """
    policy = load_policy(root)
    desired = resolve_policy_files(root, policy)
    files = state["files"]
    report: list[ReconcileEvent] = []

    for rel in list(files.keys()):
        entry = files[rel]
        if entry.get("source") == "policy" and rel not in desired:
            full = os.path.join(root, rel)
            if entry.get("locked") and os.path.exists(full):
                unprotect_path(full, entry)
            del files[rel]
            report.append(ReconcileEvent("removed", rel))

    for rel, resolved in desired.items():
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            continue
        if not check_symlink_safety(full, root):
            sys.stderr.write(f"warning: refusing to protect symlink outside repo root: {rel}\n")
            continue
        entry = files.get(rel)
        desired_scope = make_scope(resolved.deny_user, resolved.deny_group)
        want_hard = hard_all or bool(entry and entry.get("hard"))
        actual_level, _ = query_path(full, entry)
        needs_apply = (
            entry is None
            or not entry.get("locked")
            or actual_level == "UNPROTECTED"
            or entry.get("scope", "all") != desired_scope
            or (want_hard and desired_scope == "all" and entry.get("level") != "HARD")
        )
        if needs_apply:
            original_mode = determine_original_mode(full, entry)
            if desired_scope != "all":
                result = protect_path_scoped(
                    full, list(resolved.deny_user), list(resolved.deny_group)
                )
            else:
                result = protect_path(full, hard=want_hard)
            new_entry: FileEntry = {
                "backend": result["backend"],
                "level": result["level"],
                "original_mode": original_mode,
                "locked": True,
                "source": "policy",
                "message": resolved.message,
                "hard": result.get("hard", False),
                "scope": desired_scope,
            }
            if "acl_user" in result:
                new_entry["acl_user"] = result["acl_user"]
            if "acl_entries" in result:
                new_entry["acl_entries"] = result["acl_entries"]
            files[rel] = new_entry
            report.append(ReconcileEvent("locked", rel, new_entry["level"]))
        else:
            # needs_apply's `entry is None` arm being false means this
            # branch only runs when entry is not None; spelled out so the
            # type checker can see it too.
            assert entry is not None
            entry["message"] = resolved.message
            entry["source"] = "policy"

    for rel, entry in list(files.items()):
        if entry.get("source") == "adhoc" and entry.get("locked"):
            full = os.path.join(root, rel)
            if not os.path.exists(full):
                continue
            actual_level, _ = query_path(full, entry)
            if actual_level == "UNPROTECTED":
                if not check_symlink_safety(full, root):
                    sys.stderr.write(
                        f"warning: refusing to protect symlink outside repo root: {rel}\n"
                    )
                    continue
                scope = entry.get("scope", "all")
                if scope != "all":
                    result = protect_path_scoped(
                        full, scope_deny_user(scope), scope_deny_group(scope)
                    )
                else:
                    result = protect_path(full, hard=entry.get("hard", False))
                entry["backend"] = result["backend"]
                entry["level"] = result["level"]
                if "acl_user" in result:
                    entry["acl_user"] = result["acl_user"]
                if "acl_entries" in result:
                    entry["acl_entries"] = result["acl_entries"]
                report.append(ReconcileEvent("relocked", rel, entry["level"]))

    return policy, report
