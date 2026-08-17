"""End-to-end CLI tests for branch-conditional rules (SPEC.md section 33).

Split out of test_cli.py to stay under the swe Python plugin pack's
600-line test-file ceiling (SPEC.md section 26), mirroring how
test_cli_scoped.py already splits out the deny-user/deny-group and regex
layered features. Uses real temp git repos and real `git checkout`/`git
worktree` invocations end to end through the CLI, not mocked branch
names - the mocked-branch-name unit coverage lives in test_policy.py and
test_claude_hook_unit.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chwrite import cli
from chwrite.state import load_state


def _commit_all(root: str, message: str = "commit") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)


def _write_policy(root: str, body: str) -> None:
    (Path(root) / ".write_protect").write_text(body)


def test_cmd_apply_locks_on_matching_branch_and_unlocks_after_checkout(
    repo: str, posix_backend: None
) -> None:
    _write_policy(repo, 'version 1\n\nprotect generated.txt branches="main,release/*"\n')
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo, check=True)

    assert cli.main(["apply", "--quiet"]) == 0
    state = load_state(repo)
    assert state["files"]["generated.txt"]["locked"] is True
    with pytest.raises(PermissionError):
        (Path(repo) / "generated.txt").write_text("nope")

    subprocess.run(["git", "checkout", "-q", "-b", "regen-branch"], cwd=repo, check=True)
    assert cli.main(["apply", "--quiet"]) == 0
    state = load_state(repo)
    assert "generated.txt" not in state["files"]
    (Path(repo) / "generated.txt").write_text("regenerated")
    assert (Path(repo) / "generated.txt").read_text() == "regenerated"


def test_cmd_status_shows_branch_and_inactive_rule_note(
    repo: str, posix_backend: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_policy(repo, 'version 1\n\nprotect generated.txt branches="main,release/*"\n')
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "checkout", "-q", "-b", "regen-branch"], cwd=repo, check=True)

    cli.main(["apply", "--quiet"])
    code = cli.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Branch: regen-branch" in out
    assert "INACTIVE ON THIS BRANCH" in out
    assert "generated.txt" in out
    assert 'branches="main,release/*"' in out


def test_cmd_status_no_inactive_note_when_rule_active(
    repo: str, posix_backend: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_policy(repo, 'version 1\n\nprotect generated.txt branches="main"\n')
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo, check=True)

    cli.main(["apply", "--quiet"])
    code = cli.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Branch: main" in out
    assert "INACTIVE ON THIS BRANCH" not in out


def test_cmd_status_no_inactive_note_when_no_branch_scoped_rules(
    repo: str, posix_backend: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_policy(repo, "version 1\n\nprotect a.txt\n")
    (Path(repo) / "a.txt").write_text("x")
    _commit_all(repo)

    cli.main(["apply", "--quiet"])
    code = cli.main(["status"])
    out = capsys.readouterr().out
    assert code == 0
    assert "INACTIVE ON THIS BRANCH" not in out


def test_cmd_check_path_reports_unprotected_on_non_matching_branch(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_policy(repo, 'version 1\n\nprotect generated.txt branches="main"\n')
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "checkout", "-q", "-b", "regen-branch"], cwd=repo, check=True)

    code = cli.main(["check-path", "generated.txt"])
    assert code == 0
    assert capsys.readouterr().err == ""


def test_cmd_check_path_blocks_with_branch_condition_in_message(
    repo: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_policy(
        repo,
        'version 1\n\nprotect generated.txt branches="main" '
        'message="Generated; do not hand-edit"\n',
    )
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo, check=True)

    code = cli.main(["check-path", "generated.txt"])
    assert code == 1
    err = capsys.readouterr().err
    assert "Generated; do not hand-edit" in err
    assert 'branches="main"' in err
    assert 'active on current branch "main"' in err


def test_cmd_lock_adhoc_wins_over_inactive_policy_branch_rule(
    repo: str, posix_backend: None
) -> None:
    """SPEC.md 33.7: an ad hoc lock is branch-agnostic and takes precedence
    regardless of whether the policy rule's branches= condition is active
    on the current branch."""
    _write_policy(repo, 'version 1\n\nprotect generated.txt branches="main"\n')
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "checkout", "-q", "-b", "regen-branch"], cwd=repo, check=True)

    cli.main(["apply", "--quiet"])  # policy rule inactive here - nothing locked yet
    assert cli.main(["lock", "generated.txt", "--message", "mid-manual-fix"]) == 0

    with pytest.raises(PermissionError):
        (Path(repo) / "generated.txt").write_text("nope")

    assert cli.main(["unlock", "generated.txt"]) == 0
    (Path(repo) / "generated.txt").write_text("now writable")
    assert (Path(repo) / "generated.txt").read_text() == "now writable"


def test_cmd_apply_idempotent_across_repeated_runs_on_same_branch(
    repo: str, posix_backend: None
) -> None:
    _write_policy(repo, 'version 1\n\nprotect generated.txt branches="main"\n')
    (Path(repo) / "generated.txt").write_text("x")
    _commit_all(repo)
    subprocess.run(["git", "branch", "-m", "main"], cwd=repo, check=True)

    reports = []
    for _ in range(5):
        cli.main(["apply", "--quiet"])
        reports.append(load_state(repo)["files"].get("generated.txt", {}).get("locked"))
    assert reports == [True] * 5
