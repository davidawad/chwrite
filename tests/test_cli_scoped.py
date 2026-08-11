"""Scoped (deny-user/deny-group) ad hoc lock + regex-rule CLI tests
(SPEC.md 28, 29). Split out of test_cli.py to stay under the swe Python
plugin pack's 600-line test-file ceiling - these are layered, more
specialized features on top of the core CLI behavior test_cli.py covers.
"""

from __future__ import annotations

import getpass
import subprocess
from pathlib import Path

import pytest

from chwrite import cli
from chwrite.errors import ChwriteError
from chwrite.state import load_state, save_state


def _commit_all(root: str, message: str = "commit") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def _write_policy(root: str, body: str) -> None:
    (Path(root) / ".chwrite").write_text(body)


# ---------------------------------------------------------------------------
# scoped (deny-user/deny-group) ad hoc locks (SPEC.md 29)
# ---------------------------------------------------------------------------


def test_cmd_lock_hard_and_deny_user_together_errors(repo: str) -> None:
    (Path(repo) / "scratch.txt").write_text("x")
    code = cli.main(["lock", "scratch.txt", "--hard", "--deny-user", "bob"])
    assert code == 2


def test_cmd_lock_bare_with_deny_user_but_no_path_errors(repo: str) -> None:
    code = cli.main(["lock", "--deny-user", "bob"])
    assert code == 2


def test_cmd_lock_scoped_dispatches_to_protect_path_scoped(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []

    def fake_protect_scoped(full_path, deny_user, deny_group):
        calls.append((full_path, deny_user, deny_group))
        return {
            "backend": "fake-acl-deny",
            "level": "ENFORCED",
            "hard": False,
            "acl_entries": ["u:bob", "g:contractors"],
        }

    monkeypatch.setattr("chwrite.cli.protect_path_scoped", fake_protect_scoped)
    (Path(repo) / "scratch.txt").write_text("x")

    code = cli.main(
        ["lock", "scratch.txt", "--deny-user", "bob", "--deny-group", "contractors,interns"]
    )
    assert code == 0
    assert calls == [(str(Path(repo) / "scratch.txt"), ["bob"], ["contractors", "interns"])]

    state = load_state(repo)
    entry = state["files"]["scratch.txt"]
    assert entry["scope"] == {"deny_user": ["bob"], "deny_group": ["contractors", "interns"]}
    assert entry["backend"] == "fake-acl-deny"
    assert entry["acl_entries"] == ["u:bob", "g:contractors"]


def test_cmd_lock_scoped_backend_refusal_propagates_as_config_error(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(full_path, deny_user, deny_group):
        raise ChwriteError("no ACL support here", 2)

    monkeypatch.setattr("chwrite.cli.protect_path_scoped", refuse)
    (Path(repo) / "scratch.txt").write_text("x")

    code = cli.main(["lock", "scratch.txt", "--deny-user", "bob"])
    assert code == 2


@pytest.mark.skipif(
    __import__("sys").platform != "darwin", reason="exercises the real macOS ACL deny backend"
)
def test_cmd_lock_deny_user_self_real_macos_blocks_and_unlock_restores(repo: str) -> None:
    """The exact scenario from the task brief: `chwrite lock <path>
    --deny-user $(whoami)` in a throwaway repo, confirming the write now
    fails and unlock restores it (SPEC.md section 29)."""
    me = getpass.getuser()
    target = Path(repo) / "scratch.txt"
    target.write_text("original")

    code = cli.main(["lock", "scratch.txt", "--deny-user", me])
    assert code == 0

    state = load_state(repo)
    entry = state["files"]["scratch.txt"]
    assert entry["scope"] == {"deny_user": [me], "deny_group": []}
    assert entry["level"] == "ENFORCED"
    assert entry["backend"] == "macos-acl-deny"

    with pytest.raises(PermissionError):
        target.write_text("changed")

    code = cli.main(["status"])
    assert code == 0

    code = cli.main(["unlock", "scratch.txt"])
    assert code == 0
    target.write_text("changed")
    assert target.read_text() == "changed"


def test_cmd_lock_deny_user_for_a_different_user_cannot_be_verified_as_non_root(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """We cannot actually create/verify denial for a genuinely different
    OS account without root (no such fixture exists on a dev laptop). What
    IS testable without root: the scoped backend is invoked with exactly
    that (non-self) name, and chwrite records/reports it accurately rather
    than silently substituting the current user - i.e. chwrite does not
    quietly narrow "a different user" into "whoever is running it"."""
    calls: list[tuple] = []

    def fake_protect_scoped(full_path, deny_user, deny_group):
        calls.append((full_path, deny_user, deny_group))
        return {"backend": "fake-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": []}

    monkeypatch.setattr("chwrite.cli.protect_path_scoped", fake_protect_scoped)
    (Path(repo) / "scratch.txt").write_text("x")

    code = cli.main(["lock", "scratch.txt", "--deny-user", "someone-else-entirely"])
    assert code == 0
    assert calls[0][1] == ["someone-else-entirely"]
    state = load_state(repo)
    assert state["files"]["scratch.txt"]["scope"]["deny_user"] == ["someone-else-entirely"]


# ---------------------------------------------------------------------------
# Linux deny-group caveat in status/doctor output (SPEC.md 29.1)
# ---------------------------------------------------------------------------


def test_cmd_status_shows_linux_deny_group_caveat(
    repo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("chwrite.diagnostics.PLATFORM", "linux")
    (Path(repo) / "a.txt").write_text("x")
    state = load_state(repo)
    state["files"]["a.txt"] = {
        "backend": "linux-acl-deny",
        "level": "ENFORCED",
        "original_mode": None,
        "locked": True,
        "source": "adhoc",
        "message": "m",
        "hard": False,
        "scope": {"deny_user": [], "deny_group": ["contractors"]},
    }
    save_state(repo, state)

    monkeypatch.setattr(
        "chwrite.diagnostics.query_path", lambda full, entry=None: ("ENFORCED", "linux-acl-deny")
    )
    code = cli.main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    assert "deny-group is best-effort on Linux" in out
    assert "a.txt" in out


def test_cmd_status_no_caveat_when_no_deny_group_active(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["status"])
    assert code == 0
    assert "deny-group is best-effort" not in capsys.readouterr().out


def test_cmd_status_no_caveat_on_macos_even_with_deny_group_scope(
    repo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("chwrite.diagnostics.PLATFORM", "macos")
    (Path(repo) / "a.txt").write_text("x")
    state = load_state(repo)
    state["files"]["a.txt"] = {
        "backend": "macos-acl-deny",
        "level": "ENFORCED",
        "original_mode": None,
        "locked": True,
        "source": "adhoc",
        "message": "m",
        "hard": False,
        "scope": {"deny_user": [], "deny_group": ["contractors"]},
    }
    save_state(repo, state)
    monkeypatch.setattr(
        "chwrite.diagnostics.query_path", lambda full, entry=None: ("ENFORCED", "macos-acl-deny")
    )

    cli.main(["status"])
    # the caveat is Linux-specific (POSIX ACL group semantics) - macOS's
    # NFSv4-style ACEs don't have the same additive-group weakness.
    assert "deny-group is best-effort" not in capsys.readouterr().out


def test_cmd_doctor_shows_linux_acl_capability_line(
    repo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("chwrite.diagnostics.PLATFORM", "linux")
    code = cli.main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "ACL support (deny-user/deny-group)" in out


def test_cmd_doctor_shows_deny_group_caveat_when_active_on_linux(
    repo: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("chwrite.diagnostics.PLATFORM", "linux")
    state = load_state(repo)
    state["files"]["a.txt"] = {
        "backend": "linux-acl-deny",
        "level": "ENFORCED",
        "original_mode": None,
        "locked": True,
        "source": "adhoc",
        "message": "m",
        "hard": False,
        "scope": {"deny_user": [], "deny_group": ["contractors"]},
    }
    save_state(repo, state)

    code = cli.main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "deny-group is best-effort on Linux" in out


# ---------------------------------------------------------------------------
# regex rules end-to-end through the CLI (SPEC.md 28)
# ---------------------------------------------------------------------------


def test_cmd_apply_locks_files_matched_by_regex_rule(repo: str, posix_backend: None) -> None:
    (Path(repo) / "migrations").mkdir()
    (Path(repo) / "migrations" / "001.sql").write_text("x")
    (Path(repo) / "other.txt").write_text("x")
    _write_policy(repo, 'version 1\n\nprotect-regex ^migrations/.*\\.sql$ message="append-only"\n')
    _commit_all(repo)

    code = cli.main(["apply"])
    assert code == 0

    state = load_state(repo)
    assert "migrations/001.sql" in state["files"]
    assert "other.txt" not in state["files"]
    assert state["files"]["migrations/001.sql"]["message"] == "append-only"

    with pytest.raises(PermissionError):
        (Path(repo) / "migrations" / "001.sql").write_text("changed")


def test_cmd_check_path_regex_rule_blocks_with_message(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (Path(repo) / "migrations").mkdir()
    (Path(repo) / "migrations" / "001.sql").write_text("x")
    _write_policy(repo, 'version 1\n\nprotect-regex ^migrations/.*\\.sql$ message="append-only"\n')
    _commit_all(repo)

    code = cli.main(["check-path", "migrations/001.sql"])
    assert code == 1
    assert "append-only" in capsys.readouterr().err
