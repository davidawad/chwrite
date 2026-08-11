"""Subcommand behavior tests for chwrite.cli (SPEC.md 21, 24, 25, 27).

Uses the `repo`/`posix_backend` fixtures from conftest.py: a throwaway git
repo with cwd chdir'd into it, and protect_path/unprotect_path/query_path
forced to the chmod-only posix_generic backend so these tests exercise
cli.py's own bookkeeping/branching logic deterministically rather than a
platform-specific OS primitive (already covered for real on macOS in
tests/backends/test_macos.py).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from chwrite import cli
from chwrite.backends.posix_generic import protect_posix, unprotect_posix
from chwrite.state import load_state


def _commit_all(root: str, message: str = "commit") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def _write_policy(root: str, body: str) -> None:
    (Path(root) / ".chwrite").write_text(body)


# ---------------------------------------------------------------------------
# init / add / remove
# ---------------------------------------------------------------------------


def test_cmd_init_creates_plain_policy(repo: str) -> None:
    code = cli.main(["init"])
    assert code == 0
    assert (Path(repo) / ".chwrite").read_text() == "version 1\n\n"


@pytest.mark.parametrize(
    ("fmt", "filename"),
    [("json", ".chwrite.json"), ("toml", ".chwrite.toml"), ("yaml", ".chwrite.yaml")],
)
def test_cmd_init_creates_other_formats(repo: str, fmt: str, filename: str) -> None:
    code = cli.main(["init", "--format", fmt])
    assert code == 0
    assert (Path(repo) / filename).is_file()


def test_cmd_init_errors_if_policy_already_exists(repo: str) -> None:
    cli.main(["init"])
    code = cli.main(["init"])
    assert code == 2


def test_cmd_add_appends_rule(repo: str, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["init"])
    code = cli.main(["add", "package-lock.json", "--message", "do not touch"])
    assert code == 0
    text = (Path(repo) / ".chwrite").read_text()
    assert 'protect package-lock.json message="do not touch"' in text
    out = capsys.readouterr().out
    assert "package-lock.json" in out


def test_cmd_add_updates_existing_rule_message(repo: str) -> None:
    cli.main(["init"])
    cli.main(["add", "foo.txt", "--message", "first"])
    cli.main(["add", "foo.txt", "--message", "second"])
    text = (Path(repo) / ".chwrite").read_text()
    assert text.count("protect foo.txt") == 1
    assert 'message="second"' in text


def test_cmd_add_without_policy_errors(repo: str) -> None:
    code = cli.main(["add", "foo.txt"])
    assert code == 2


def test_cmd_add_rejects_traversal_pathspec(repo: str) -> None:
    cli.main(["init"])
    code = cli.main(["add", "../outside.txt"])
    assert code == 2


def test_cmd_remove_removes_rule(repo: str) -> None:
    cli.main(["init"])
    cli.main(["add", "foo.txt"])
    code = cli.main(["remove", "foo.txt"])
    assert code == 0
    text = (Path(repo) / ".chwrite").read_text()
    assert "foo.txt" not in text


def test_cmd_remove_no_matching_rule_returns_1(repo: str) -> None:
    cli.main(["init"])
    code = cli.main(["remove", "nonexistent.txt"])
    assert code == 1


def test_cmd_remove_without_policy_errors(repo: str) -> None:
    code = cli.main(["remove", "foo.txt"])
    assert code == 2


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


def test_cmd_apply_locks_policy_files(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)

    code = cli.main(["apply"])
    assert code == 0

    state = load_state(repo)
    entry = state["files"]["protected.txt"]
    assert entry["locked"] is True
    assert entry["level"] == "READONLY"

    full = Path(repo) / "protected.txt"
    with pytest.raises(PermissionError):
        full.write_text("changed")


def test_cmd_apply_is_idempotent_ten_times(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)

    codes = [cli.main(["apply", "--quiet"]) for _ in range(10)]
    assert all(c == 0 for c in codes)

    state = load_state(repo)
    assert state["files"]["protected.txt"]["locked"] is True


def test_cmd_apply_quiet_suppresses_output(repo: str, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["apply", "--quiet"])
    assert capsys.readouterr().out == ""


def test_cmd_apply_prints_nothing_to_do_when_no_policy(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["apply"])
    assert code == 0
    assert "nothing to do" in capsys.readouterr().out


def test_cmd_apply_unprotects_file_removed_from_policy(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    _write_policy(repo, "version 1\n")
    code = cli.main(["apply"])
    assert code == 0
    state = load_state(repo)
    assert "protected.txt" not in state["files"]
    # write should now succeed - protection was lifted
    (Path(repo) / "protected.txt").write_text("changed")
    assert (Path(repo) / "protected.txt").read_text() == "changed"


# ---------------------------------------------------------------------------
# lock
# ---------------------------------------------------------------------------


def test_cmd_lock_all_locks_policy_files(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)

    code = cli.main(["lock"])
    assert code == 0
    state = load_state(repo)
    assert state["files"]["protected.txt"]["locked"] is True


def test_cmd_lock_adhoc_single_path(
    repo: str, posix_backend: None, capsys: pytest.CaptureFixture[str]
) -> None:
    (Path(repo) / "scratch.txt").write_text("x")
    code = cli.main(["lock", "scratch.txt", "--message", "mid-refactor"])
    assert code == 0
    state = load_state(repo)
    entry = state["files"]["scratch.txt"]
    assert entry["source"] == "adhoc"
    assert entry["message"] == "mid-refactor"
    assert "locked scratch.txt" in capsys.readouterr().out


def test_cmd_lock_adhoc_default_message(repo: str, posix_backend: None) -> None:
    (Path(repo) / "scratch.txt").write_text("x")
    cli.main(["lock", "scratch.txt"])
    state = load_state(repo)
    assert state["files"]["scratch.txt"]["message"]


def test_cmd_lock_adhoc_reuses_prior_adhoc_message(repo: str, posix_backend: None) -> None:
    (Path(repo) / "scratch.txt").write_text("x")
    cli.main(["lock", "scratch.txt", "--message", "keep me"])
    cli.main(["unlock", "scratch.txt"])
    cli.main(["lock", "scratch.txt"])
    state = load_state(repo)
    assert state["files"]["scratch.txt"]["message"] == "keep me"


def test_cmd_lock_adhoc_nonexistent_path_errors(repo: str) -> None:
    code = cli.main(["lock", "does-not-exist.txt"])
    assert code == 2


def test_cmd_lock_adhoc_refuses_symlink_outside_repo(repo: str, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("x")
    link = Path(repo) / "link.txt"
    link.symlink_to(outside)
    code = cli.main(["lock", "link.txt"])
    assert code == 2
    outside.unlink(missing_ok=True)


def test_cmd_lock_hard_flag_reports_failure_when_not_hard(
    repo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # posix_generic never provides HARD, so `--hard` should surface exit 1.
    monkeypatch.setattr("chwrite.cli.protect_path", protect_posix)
    monkeypatch.setattr("chwrite.cli.unprotect_path", unprotect_posix)
    (Path(repo) / "scratch.txt").write_text("x")
    code = cli.main(["lock", "scratch.txt", "--hard"])
    assert code == 1


def test_cmd_lock_all_hard_reports_failure_when_not_hard(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    code = cli.main(["lock", "--hard"])
    assert code == 1


def test_cmd_lock_all_prints_nothing_to_do(
    repo: str, posix_backend: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["lock"])
    assert code == 0
    assert "nothing to do" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# unlock / unlocked
# ---------------------------------------------------------------------------


def test_cmd_unlock_single_path(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    code = cli.main(["unlock", "protected.txt"])
    assert code == 0
    (Path(repo) / "protected.txt").write_text("changed")
    assert (Path(repo) / "protected.txt").read_text() == "changed"
    state = load_state(repo)
    assert state["files"]["protected.txt"]["locked"] is False


def test_cmd_unlock_not_currently_locked_is_a_noop(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["unlock", "never-locked.txt"])
    assert code == 0
    assert "is not currently locked" in capsys.readouterr().out


def test_cmd_unlock_requires_path_or_all(repo: str) -> None:
    code = cli.main(["unlock"])
    assert code == 2


def test_cmd_unlock_all(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect a.txt\nprotect b.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    (Path(repo) / "b.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    code = cli.main(["unlock", "--all"])
    assert code == 0
    state = load_state(repo)
    assert all(not e["locked"] for e in state["files"].values())
    (Path(repo) / "a.txt").write_text("changed")
    (Path(repo) / "b.txt").write_text("changed")


def test_cmd_unlocked_runs_command_and_reapplies(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    code = cli.main(["unlocked", "--", "python3", "-c", "1"])
    assert code == 0
    state = load_state(repo)
    assert state["files"]["protected.txt"]["locked"] is True
    with pytest.raises(PermissionError):
        (Path(repo) / "protected.txt").write_text("changed")


def test_cmd_unlocked_propagates_nonzero_exit_and_still_reapplies(
    repo: str, posix_backend: None
) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    code = cli.main(["unlocked", "--", "python3", "-c", "import sys; sys.exit(7)"])
    assert code == 7
    state = load_state(repo)
    assert state["files"]["protected.txt"]["locked"] is True


def test_cmd_unlocked_requires_a_command(repo: str) -> None:
    code = cli.main(["unlocked"])
    assert code == 2


def test_cmd_unlocked_writes_actually_succeed_mid_command(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    protected = Path(repo) / "protected.txt"
    protected.write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    script = f"open({str(protected)!r}, 'w').write('written-during-unlock')"
    code = cli.main(["unlocked", "--", "python3", "-c", script])
    assert code == 0
    assert protected.read_text() == "written-during-unlock"


# ---------------------------------------------------------------------------
# status / verify
# ---------------------------------------------------------------------------


def test_cmd_status_reports_protected_and_violations(
    repo: str, posix_backend: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\nprotect gone.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    (Path(repo) / "gone.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    (Path(repo) / "gone.txt").unlink()  # not deletable with chmod-only protection

    code = cli.main(["status"])
    out = capsys.readouterr().out
    assert "1 protected" in out
    assert "1 violations" in out
    assert code == 1


def test_cmd_status_shows_policy_path_and_none(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Policy: (none)" in out
    assert "0 protected" in out


def test_cmd_verify_ok_when_clean(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])

    code = cli.main(["verify"])
    assert code == 0


def test_cmd_verify_detects_deleted_file(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])
    (Path(repo) / "protected.txt").unlink()

    code = cli.main(["verify", "--quiet"])
    assert code == 1


def test_cmd_verify_detects_flags_removed(repo: str, posix_backend: None) -> None:
    _write_policy(repo, "version 1\n\nprotect protected.txt\n")
    (Path(repo) / "protected.txt").write_text("x")
    _commit_all(repo)
    cli.main(["apply", "--quiet"])
    (Path(repo) / "protected.txt").chmod(0o644)  # simulate flags-removed drift

    code = cli.main(["verify", "--quiet"])
    assert code == 1


def test_cmd_verify_quiet_suppresses_ok_message(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["verify", "--quiet"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_cmd_verify_prints_ok_when_not_quiet(repo: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["verify"])
    assert code == 0
    assert "chwrite verify: OK" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_cmd_doctor_runs_inside_repo(repo: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "chwrite doctor" in out
    assert f"Repository: {repo}" in out


def test_cmd_doctor_runs_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)
    code = cli.main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Not currently inside a git repository" in out


# ---------------------------------------------------------------------------
# check-path (argparse wiring; deeper coverage in test_claude_hook_unit.py)
# ---------------------------------------------------------------------------


def test_cmd_check_path_unprotected(repo: str) -> None:
    (Path(repo) / "normal.txt").write_text("x")
    code = cli.main(["check-path", "normal.txt"])
    assert code == 0


def test_cmd_check_path_requires_path_or_claude_hook(repo: str) -> None:
    code = cli.main(["check-path"])
    assert code == 2


# ---------------------------------------------------------------------------
# main() entrypoint / error mapping
# ---------------------------------------------------------------------------


def test_main_maps_chwrite_error_to_exit_code(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["add", "foo.txt"])  # no policy file yet
    assert code == 2
    assert "chwrite:" in capsys.readouterr().err


def test_main_help_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_main_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


def test_build_parser_all_subcommands_registered() -> None:
    parser = cli.build_parser()
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    names = set(sub_action.choices.keys())
    assert names == {
        "init",
        "add",
        "remove",
        "apply",
        "lock",
        "unlock",
        "unlocked",
        "status",
        "verify",
        "check-path",
        "doctor",
    }
