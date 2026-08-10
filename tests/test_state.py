"""State round-trip tests (SPEC.md section 9)."""

from __future__ import annotations

import subprocess

from chwrite.state import (
    determine_original_mode,
    load_state,
    make_scope,
    save_state,
    scope_deny_group,
    scope_deny_user,
)


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return str(tmp_path)


def test_load_state_empty_when_missing(tmp_path) -> None:
    root = _init_repo(tmp_path)
    state = load_state(root)
    assert state == {"version": 1, "files": {}}


def test_save_and_reload_round_trips(tmp_path) -> None:
    root = _init_repo(tmp_path)
    state = load_state(root)
    state["files"]["foo.txt"] = {
        "backend": "posix-chmod",
        "level": "READONLY",
        "original_mode": 0o644,
        "locked": True,
        "source": "policy",
        "message": "hi",
        "hard": False,
    }
    save_state(root, state)
    reloaded = load_state(root)
    assert reloaded["files"]["foo.txt"]["original_mode"] == 0o644
    assert reloaded["files"]["foo.txt"]["locked"] is True


def test_determine_original_mode_prefers_stored_value(tmp_path) -> None:
    f = tmp_path / "foo.txt"
    f.write_text("hi")
    assert determine_original_mode(str(f), {"original_mode": 0o600}) == 0o600


def test_determine_original_mode_falls_back_to_current(tmp_path) -> None:
    f = tmp_path / "foo.txt"
    f.write_text("hi")
    f.chmod(0o644)
    assert determine_original_mode(str(f), None) == 0o644


def test_make_scope_returns_all_when_empty() -> None:
    assert make_scope([], []) == "all"


def test_make_scope_returns_dict_when_deny_user_set() -> None:
    assert make_scope(["bob"], []) == {"deny_user": ["bob"], "deny_group": []}


def test_make_scope_returns_dict_when_deny_group_set() -> None:
    assert make_scope([], ["contractors"]) == {"deny_user": [], "deny_group": ["contractors"]}


def test_scope_deny_user_all_scope_is_empty() -> None:
    assert scope_deny_user("all") == []


def test_scope_deny_user_extracts_names() -> None:
    assert scope_deny_user({"deny_user": ["bob"], "deny_group": []}) == ["bob"]


def test_scope_deny_group_all_scope_is_empty() -> None:
    assert scope_deny_group("all") == []


def test_scope_deny_group_extracts_names() -> None:
    assert scope_deny_group({"deny_user": [], "deny_group": ["contractors"]}) == ["contractors"]
