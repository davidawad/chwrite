"""In-process unit tests for chwrite.claude_hook (SPEC.md 24.4, 25, 27).

Complements tests/test_claude_hook.py, which exercises check-path/verify
exit codes end-to-end through the bundled chwrite.py subprocess; these
call cmd_check_path/_claude_hook_main/install_claude_hook directly so
branches that are awkward to hit through a subprocess boundary (malformed
stdin JSON, ad hoc-lock-message precedence, non-destructive settings.json
merging) are covered without paying subprocess overhead on every case.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
from pathlib import Path

import pytest

from chwrite.claude_hook import (
    CLAUDE_HOOK_COMMAND,
    CLAUDE_HOOK_MATCHER,
    _claude_hook_main,
    _protection_message,
    cmd_check_path,
    install_claude_hook,
)
from chwrite.errors import ChwriteError
from chwrite.state import load_state, save_state


def _init_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return str(tmp_path)


def _repo_with_policy(tmp_path: Path) -> str:
    root = _init_repo(tmp_path)
    (Path(root) / ".chwrite").write_text(
        'version 1\n\nprotect protected.txt message="policy message"\n'
    )
    (Path(root) / "protected.txt").write_text("x")
    (Path(root) / "normal.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


# ---------------------------------------------------------------------------
# cmd_check_path
# ---------------------------------------------------------------------------


def test_cmd_check_path_protected_by_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    code = cmd_check_path(argparse.Namespace(path="protected.txt", claude_hook=False))
    assert code == 1
    assert "policy message" in capsys.readouterr().err


def test_cmd_check_path_unprotected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    code = cmd_check_path(argparse.Namespace(path="normal.txt", claude_hook=False))
    assert code == 0


def test_cmd_check_path_requires_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_repo(tmp_path)
    monkeypatch.chdir(root)
    with pytest.raises(ChwriteError) as exc_info:
        cmd_check_path(argparse.Namespace(path=None, claude_hook=False))
    assert exc_info.value.code == 2


def test_cmd_check_path_outside_repo_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    code = cmd_check_path(argparse.Namespace(path="whatever.txt", claude_hook=False))
    assert code == 0


def test_cmd_check_path_path_resolves_outside_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    sibling = tmp_path / "sibling.txt"
    sibling.write_text("x")
    code = cmd_check_path(argparse.Namespace(path=str(sibling), claude_hook=False))
    assert code == 0


def test_cmd_check_path_adhoc_lock_message_wins_over_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    state = load_state(root)
    state["files"]["protected.txt"] = {
        "backend": "posix-chmod",
        "level": "READONLY",
        "original_mode": 0o644,
        "locked": True,
        "source": "adhoc",
        "message": "ad hoc wins",
        "hard": False,
    }
    save_state(root, state)

    code = cmd_check_path(argparse.Namespace(path="protected.txt", claude_hook=False))
    assert code == 1
    assert "ad hoc wins" in capsys.readouterr().err


def test_protection_message_none_when_not_locked_and_no_policy_rule(
    tmp_path: Path,
) -> None:
    root = _init_repo(tmp_path)
    (Path(root) / "free.txt").write_text("x")
    assert _protection_message(root, "free.txt") is None


# ---------------------------------------------------------------------------
# _claude_hook_main / --claude-hook stdin protocol
# ---------------------------------------------------------------------------


def test_claude_hook_main_malformed_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    code = _claude_hook_main()
    assert code == 2
    assert "malformed hook payload" in capsys.readouterr().err


def test_claude_hook_main_no_file_path_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": {}})))
    assert _claude_hook_main() == 0


def test_claude_hook_main_non_dict_payload_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(["not", "a", "dict"])))
    assert _claude_hook_main() == 0


def test_claude_hook_main_blocks_protected_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    payload = {"tool_input": {"file_path": str(Path(root) / "protected.txt")}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    code = _claude_hook_main()
    assert code == 2
    assert "policy message" in capsys.readouterr().err


def test_claude_hook_main_allows_unprotected_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    payload = {"tool_input": {"file_path": str(Path(root) / "normal.txt")}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert _claude_hook_main() == 0


def test_claude_hook_main_uses_notebook_path_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo_with_policy(tmp_path)
    monkeypatch.chdir(root)
    payload = {"tool_input": {"notebook_path": str(Path(root) / "protected.txt")}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert _claude_hook_main() == 2


def test_claude_hook_main_outside_repo_allows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    payload = {"tool_input": {"file_path": str(outside / "whatever.txt")}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    assert _claude_hook_main() == 0


def test_cmd_check_path_dispatches_to_claude_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tool_input": {}})))
    code = cmd_check_path(argparse.Namespace(path=None, claude_hook=True))
    assert code == 0


# ---------------------------------------------------------------------------
# install_claude_hook - non-destructive settings.json merge (SPEC.md 25.1)
# ---------------------------------------------------------------------------


def test_install_claude_hook_creates_settings_from_scratch(tmp_path: Path) -> None:
    install_claude_hook(str(tmp_path))
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    pretool = settings["hooks"]["PreToolUse"]
    assert len(pretool) == 1
    assert pretool[0]["matcher"] == CLAUDE_HOOK_MATCHER
    assert pretool[0]["hooks"] == [{"type": "command", "command": CLAUDE_HOOK_COMMAND}]


def test_install_claude_hook_does_not_clobber_existing_hooks(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "SomeOtherTool",
                    "hooks": [{"type": "command", "command": "some-other-hook"}],
                }
            ]
        },
        "unrelatedTopLevelKey": "keep-me",
    }
    (claude_dir / "settings.json").write_text(json.dumps(existing))

    install_claude_hook(str(tmp_path))

    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["unrelatedTopLevelKey"] == "keep-me"
    pretool = settings["hooks"]["PreToolUse"]
    assert len(pretool) == 2
    matchers = {block["matcher"] for block in pretool}
    assert matchers == {"SomeOtherTool", CLAUDE_HOOK_MATCHER}
    # the pre-existing hook entry itself is untouched
    other = next(b for b in pretool if b["matcher"] == "SomeOtherTool")
    assert other["hooks"] == [{"type": "command", "command": "some-other-hook"}]


def test_install_claude_hook_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install_claude_hook(str(tmp_path))
    install_claude_hook(str(tmp_path))
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert len(settings["hooks"]["PreToolUse"]) == 1
    assert "already has a chwrite hook" in capsys.readouterr().out


def test_install_claude_hook_rejects_malformed_json(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{not valid json")
    with pytest.raises(ChwriteError) as exc_info:
        install_claude_hook(str(tmp_path))
    assert exc_info.value.code == 2


def test_install_claude_hook_rejects_non_object_top_level(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("[]")
    with pytest.raises(ChwriteError):
        install_claude_hook(str(tmp_path))


def test_install_claude_hook_rejects_non_object_hooks_key(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"hooks": "not-an-object"}))
    with pytest.raises(ChwriteError):
        install_claude_hook(str(tmp_path))


def test_install_claude_hook_rejects_non_list_pretooluse(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": "not-a-list"}}))
    with pytest.raises(ChwriteError):
        install_claude_hook(str(tmp_path))


def test_install_claude_hook_empty_file_treated_as_empty_object(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("")
    install_claude_hook(str(tmp_path))
    settings = json.loads((claude_dir / "settings.json").read_text())
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == CLAUDE_HOOK_MATCHER


# ---------------------------------------------------------------------------
# regex rules (SPEC.md section 28) and last-matching-rule-wins precedence
# ---------------------------------------------------------------------------


def _repo_with_regex_policy(tmp_path: Path) -> str:
    root = _init_repo(tmp_path)
    (Path(root) / "migrations").mkdir()
    (Path(root) / "migrations" / "001.sql").write_text("x")
    (Path(root) / "migrations" / "readme.md").write_text("x")
    (Path(root) / ".chwrite").write_text(
        'version 1\n\nprotect-regex ^migrations/.*\\.sql$ message="append-only"\n'
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return root


def test_protection_message_regex_rule_matches(tmp_path: Path) -> None:
    root = _repo_with_regex_policy(tmp_path)
    assert _protection_message(root, "migrations/001.sql") == "append-only"


def test_protection_message_regex_rule_does_not_match_other_files(tmp_path: Path) -> None:
    root = _repo_with_regex_policy(tmp_path)
    assert _protection_message(root, "migrations/readme.md") is None


def test_protection_message_last_matching_policy_rule_wins(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    (Path(root) / "a.txt").write_text("x")
    (Path(root) / ".chwrite").write_text(
        'version 1\n\nprotect a.txt message="first"\nprotect a.txt message="second"\n'
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    assert _protection_message(root, "a.txt") == "second"


def test_cmd_check_path_regex_rule_via_stdin_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo_with_regex_policy(tmp_path)
    monkeypatch.chdir(root)
    payload = {"tool_input": {"file_path": str(Path(root) / "migrations" / "001.sql")}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    code = _claude_hook_main()
    assert code == 2
    assert "append-only" in capsys.readouterr().err
