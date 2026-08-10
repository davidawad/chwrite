"""Direct tests for chwrite.reconcile (SPEC.md 7-8, 18, 27).

Uses the `repo`/`posix_backend` fixtures from conftest.py so this exercises
reconcile()'s own branching (drop-removed, (re)lock desired, self-heal ad
hoc) deterministically via the chmod-only posix_generic backend.
"""

from __future__ import annotations

import getpass
import subprocess
from pathlib import Path

import pytest

from chwrite.backends.linux import protect_linux, query_linux, unprotect_linux
from chwrite.backends.macos import unprotect_macos_scoped
from chwrite.reconcile import reconcile
from chwrite.state import load_state, save_state


def _commit_all(root: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=root, check=True)


def test_reconcile_locks_new_policy_files(repo: str, posix_backend: None) -> None:
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    policy, report = reconcile(repo, state)
    save_state(repo, state)

    assert policy is not None
    assert len(report) == 1
    assert report[0].kind == "locked"
    assert report[0].rel == "a.txt"
    assert state["files"]["a.txt"]["locked"] is True


def test_reconcile_is_idempotent(repo: str, posix_backend: None) -> None:
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    reports = []
    for _ in range(10):
        _, report = reconcile(repo, state)
        save_state(repo, state)
        reports.append(report)

    assert len(reports[0]) == 1
    assert all(r == [] for r in reports[1:])


def test_reconcile_drops_entries_removed_from_policy(repo: str, posix_backend: None) -> None:
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    reconcile(repo, state)
    save_state(repo, state)
    assert "a.txt" in state["files"]

    (Path(repo) / ".chwrite").write_text("version 1\n")
    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert "a.txt" not in state["files"]
    assert any(e.kind == "removed" and e.rel == "a.txt" for e in report)
    # write should succeed now that protection was lifted
    (Path(repo) / "a.txt").write_text("changed")
    assert (Path(repo) / "a.txt").read_text() == "changed"


def test_reconcile_hard_all_relocks_to_hard(repo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # linux's backend actually distinguishes HARD (chattr) from READONLY
    # (chmod) via the `hard` argument, unlike posix_generic which never
    # offers HARD - use it (mocked chattr) to exercise reconcile's
    # want_hard/hard_all relock path meaningfully.
    monkeypatch.setattr("chwrite.reconcile.protect_path", protect_linux)
    monkeypatch.setattr("chwrite.reconcile.unprotect_path", unprotect_linux)

    def _query_linux_entry_aware(full_path, entry=None):
        del entry
        return query_linux(full_path)

    monkeypatch.setattr("chwrite.reconcile.query_path", _query_linux_entry_aware)
    monkeypatch.setattr(
        "chwrite.backends.linux.shutil.which",
        lambda name: "/usr/bin/chattr" if name == "chattr" else None,
    )

    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        # Only intercept chattr - `chwrite.backends.linux.subprocess.run`
        # patches the subprocess module object process-wide (linux.py does
        # `import subprocess`, not `from subprocess import run`), so
        # anything else (git ls-files, etc.) must be passed through to the
        # real implementation.
        if args[0] != "chattr":
            return real_run(args, **kwargs)
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("chwrite.backends.linux.subprocess.run", fake_run)

    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    _, report = reconcile(repo, state, hard_all=False)
    save_state(repo, state)
    assert state["files"]["a.txt"]["level"] == "READONLY"

    state = load_state(repo)
    _, report = reconcile(repo, state, hard_all=True)
    save_state(repo, state)
    assert state["files"]["a.txt"]["level"] == "HARD"
    assert any(e.kind == "relocked" for e in report) or any(e.kind == "locked" for e in report)
    assert ["chattr", "+i", str(Path(repo) / "a.txt")] in calls


def test_reconcile_self_heals_adhoc_lock_that_drifted_unprotected(
    repo: str, posix_backend: None
) -> None:
    (Path(repo) / "scratch.txt").write_text("x")
    state = load_state(repo)
    state["files"]["scratch.txt"] = {
        "backend": "posix-chmod",
        "level": "READONLY",
        "original_mode": 0o644,
        "locked": True,
        "source": "adhoc",
        "message": "manual",
        "hard": False,
    }
    save_state(repo, state)
    # Simulate drift: something restored the write bit without going
    # through chwrite unlock.
    (Path(repo) / "scratch.txt").chmod(0o644)

    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert any(e.kind == "relocked" and e.rel == "scratch.txt" for e in report)
    with pytest.raises(PermissionError):
        (Path(repo) / "scratch.txt").write_text("changed")


def test_reconcile_skips_missing_policy_files(repo: str, posix_backend: None) -> None:
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect missing.txt\n")
    # missing.txt intentionally never created/committed - not tracked, so
    # `git ls-files` never resolves it and reconcile has nothing to do.
    _commit_all(repo)

    state = load_state(repo)
    _, report = reconcile(repo, state)
    assert report == []


def test_reconcile_warns_and_skips_symlink_outside_repo(
    repo: str, posix_backend: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path.parent / "reconcile-outside-target.txt"
    outside.write_text("x")
    link = Path(repo) / "link.txt"
    link.symlink_to(outside)
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect link.txt\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "commit"], cwd=repo, check=True)

    state = load_state(repo)
    _, report = reconcile(repo, state)
    assert report == []
    assert "refusing to protect symlink" in capsys.readouterr().err
    outside.unlink(missing_ok=True)


def test_reconcile_updates_message_for_already_locked_policy_file(
    repo: str, posix_backend: None
) -> None:
    (Path(repo) / ".chwrite").write_text('version 1\n\nprotect a.txt message="first"\n')
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    reconcile(repo, state)
    save_state(repo, state)
    assert state["files"]["a.txt"]["message"] == "first"

    (Path(repo) / ".chwrite").write_text('version 1\n\nprotect a.txt message="second"\n')
    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert report == []  # already locked at the right level - no OS calls
    assert state["files"]["a.txt"]["message"] == "second"


# ---------------------------------------------------------------------------
# scoped (deny-user/deny-group) policy rules (SPEC.md 29)
# ---------------------------------------------------------------------------


def test_reconcile_policy_scoped_rule_dispatches_to_scoped_backend(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []

    def fake_protect_scoped(full_path, deny_user, deny_group):
        calls.append((full_path, deny_user, deny_group))
        return {
            "backend": "fake-acl-deny",
            "level": "ENFORCED",
            "hard": False,
            "acl_entries": ["u:bob"],
        }

    monkeypatch.setattr("chwrite.reconcile.protect_path_scoped", fake_protect_scoped)
    monkeypatch.setattr(
        "chwrite.reconcile.query_path", lambda full, entry=None: ("UNPROTECTED", None)
    )
    monkeypatch.setattr("chwrite.reconcile.unprotect_path", lambda full, entry: None)

    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt deny-user=bob\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert len(calls) == 1
    assert calls[0][1] == ["bob"]
    entry = state["files"]["a.txt"]
    assert entry["scope"] == {"deny_user": ["bob"], "deny_group": []}
    assert entry["backend"] == "fake-acl-deny"
    assert entry["acl_entries"] == ["u:bob"]
    assert report[0].kind == "locked"


def test_reconcile_scope_change_from_blanket_to_scoped_triggers_reapply(
    repo: str, posix_backend: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    reconcile(repo, state)
    save_state(repo, state)
    assert state["files"]["a.txt"]["scope"] == "all"

    calls: list[tuple] = []

    def fake_protect_scoped(full_path, deny_user, deny_group):
        calls.append((full_path, deny_user, deny_group))
        return {"backend": "fake-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": []}

    monkeypatch.setattr("chwrite.reconcile.protect_path_scoped", fake_protect_scoped)
    (Path(repo) / ".chwrite").write_text("version 1\n\nprotect a.txt deny-user=bob\n")
    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert len(calls) == 1
    assert state["files"]["a.txt"]["scope"] == {"deny_user": ["bob"], "deny_group": []}
    assert report[0].kind == "locked"


def test_reconcile_scoped_adhoc_lock_self_heals_via_scoped_backend(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (Path(repo) / "scratch.txt").write_text("x")
    state = load_state(repo)
    state["files"]["scratch.txt"] = {
        "backend": "fake-acl-deny",
        "level": "ENFORCED",
        "original_mode": 0o644,
        "locked": True,
        "source": "adhoc",
        "message": "manual",
        "hard": False,
        "scope": {"deny_user": ["bob"], "deny_group": []},
    }
    save_state(repo, state)

    calls: list[tuple] = []

    def fake_protect_scoped(full_path, deny_user, deny_group):
        calls.append((full_path, deny_user, deny_group))
        return {"backend": "fake-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": []}

    monkeypatch.setattr("chwrite.reconcile.protect_path_scoped", fake_protect_scoped)
    monkeypatch.setattr(
        "chwrite.reconcile.query_path", lambda full, entry=None: ("UNPROTECTED", None)
    )

    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert len(calls) == 1
    assert calls[0][1] == ["bob"]
    assert any(e.kind == "relocked" and e.rel == "scratch.txt" for e in report)


@pytest.mark.skipif(
    __import__("sys").platform != "darwin", reason="exercises the real macOS ACL deny backend"
)
def test_reconcile_real_macos_deny_user_end_to_end(repo: str) -> None:
    """Real end-to-end acceptance-style test: a `protect ... deny-user=<self>`
    policy rule actually results in a blocked write via the real macOS ACL
    backend, and unlocking restores it (SPEC.md section 29 + 23's pattern)."""
    me = getpass.getuser()
    (Path(repo) / ".chwrite").write_text(f"version 1\n\nprotect a.txt deny-user={me}\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    state = load_state(repo)
    _, report = reconcile(repo, state)
    save_state(repo, state)

    assert report[0].kind == "locked"
    assert report[0].level == "ENFORCED"
    entry = state["files"]["a.txt"]
    assert entry["scope"] == {"deny_user": [me], "deny_group": []}
    assert entry["backend"] == "macos-acl-deny"

    with pytest.raises(PermissionError):
        (Path(repo) / "a.txt").write_text("changed")

    unprotect_macos_scoped(str(Path(repo) / "a.txt"), entry)
    (Path(repo) / "a.txt").write_text("changed")
    assert (Path(repo) / "a.txt").read_text() == "changed"
