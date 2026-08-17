"""Tests for chwrite.gitutil path-safety helpers and git process wrapping
(SPEC.md 5, 18, 27, 33)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chwrite import gitutil
from chwrite.errors import ChwriteError
from chwrite.gitutil import (
    branch_matches,
    current_branch,
    pathspec_matches,
    repo_root,
    resolve_target_path,
    run_git,
    strip_pathspec_magic,
    try_repo_root,
    validate_pathspec,
    validate_resolved_path,
)


def test_validate_pathspec_rejects_absolute_path() -> None:
    with pytest.raises(ChwriteError):
        validate_pathspec("/etc/passwd", "/repo")


def test_validate_pathspec_rejects_traversal() -> None:
    with pytest.raises(ChwriteError):
        validate_pathspec("../outside", "/repo")


def test_validate_pathspec_rejects_traversal_with_glob_magic() -> None:
    with pytest.raises(ChwriteError):
        validate_pathspec(":(glob)../outside/**", "/repo")


def test_validate_pathspec_accepts_normal_pattern() -> None:
    validate_pathspec("package-lock.json", "/repo")
    validate_pathspec(":(glob)migrations/**", "/repo")


def test_validate_pathspec_rejects_empty() -> None:
    with pytest.raises(ChwriteError):
        validate_pathspec("", "/repo")


def test_validate_pathspec_rejects_windows_drive_absolute() -> None:
    with pytest.raises(ChwriteError):
        validate_pathspec("C:\\Windows\\foo.txt", "/repo")


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("package-lock.json", "package-lock.json", True),
        ("package-lock.json", "other.json", False),
        (":(glob)migrations/**", "migrations/002.sql", True),
        (":(glob)migrations/**", "migrations/nested/002.sql", True),
        (":(glob)migrations/**", "other/002.sql", False),
        ("migrations", "migrations/002.sql", True),
        ("migrations", "migrations2/002.sql", False),
    ],
)
def test_pathspec_matches(pattern: str, path: str, expected: bool) -> None:
    assert pathspec_matches(pattern, path) is expected


def test_run_git_missing_executable_raises(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(gitutil.subprocess, "run", fake_run)
    with pytest.raises(ChwriteError) as exc_info:
        run_git(["status"])
    assert exc_info.value.code == 2


def test_run_git_check_raises_on_nonzero_exit(tmp_path) -> None:
    with pytest.raises(ChwriteError):
        run_git(["not-a-real-git-subcommand"], cwd=str(tmp_path))


def test_run_git_no_check_returns_completed_process(tmp_path) -> None:
    proc = run_git(["not-a-real-git-subcommand"], cwd=str(tmp_path), check=False)
    assert proc.returncode != 0


def test_repo_root_raises_outside_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ChwriteError):
        repo_root()


def test_try_repo_root_none_outside_repo(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert try_repo_root() is None


def test_try_repo_root_raises_when_git_missing(monkeypatch) -> None:
    monkeypatch.setattr(gitutil.shutil, "which", lambda _name: None)
    with pytest.raises(ChwriteError):
        try_repo_root()


def test_strip_pathspec_magic_malformed_raises() -> None:
    with pytest.raises(ChwriteError):
        strip_pathspec_magic(":(glob-unterminated")


def test_strip_pathspec_magic_exclude_shorthand() -> None:
    bare, magic = strip_pathspec_magic(":!foo.txt")
    assert bare == "foo.txt"
    assert magic == {"exclude"}


def test_validate_resolved_path_rejects_absolute() -> None:
    with pytest.raises(ChwriteError):
        validate_resolved_path("/etc/passwd", "/repo")


def test_validate_resolved_path_rejects_traversal() -> None:
    with pytest.raises(ChwriteError):
        validate_resolved_path("../outside", "/repo")


def test_validate_resolved_path_accepts_normal_path() -> None:
    validate_resolved_path("foo/bar.txt", "/repo")


def test_resolve_target_path_outside_repo_returns_none(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    assert resolve_target_path(str(outside), "/definitely/not/" + str(tmp_path)) is None


# ---------------------------------------------------------------------------
# current_branch() / branch_matches() (SPEC.md section 33)
# ---------------------------------------------------------------------------


def test_current_branch_returns_branch_name(repo: str) -> None:
    # `repo` (conftest.py) is a freshly `git init`'d repo - no commit yet,
    # but symbolic-ref still resolves the pending branch name even on an
    # unborn HEAD (only a genuinely *detached* HEAD makes it fail).
    name = current_branch(repo)
    assert name is not None
    assert name  # non-empty


def test_current_branch_none_on_detached_head(repo: str) -> None:
    (Path(repo) / "a.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=repo, check=True)
    sha = (
        subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )
    subprocess.run(["git", "checkout", "-q", sha], cwd=repo, check=True)

    assert current_branch(repo) is None


def test_current_branch_per_worktree(repo: str, tmp_path) -> None:
    (Path(repo) / "a.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=repo, check=True)
    subprocess.run(["git", "branch", "other-branch"], cwd=repo, check=True)

    wt_dir = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(wt_dir), "other-branch"], cwd=repo, check=True
    )

    main_branch = current_branch(repo)
    wt_branch = current_branch(str(wt_dir))
    assert wt_branch == "other-branch"
    assert main_branch != wt_branch


@pytest.mark.parametrize(
    ("branch", "patterns", "expected"),
    [
        ("main", (), True),  # no branches= condition -> always matches
        ("main", ("main",), True),
        ("main", ("release/*",), False),
        ("release/1.0", ("release/*",), True),
        # fnmatch's `*` matches any character sequence including `/` (it is
        # not a path-aware glob) - `release/*` matches nested branch names
        # like `release/1.0/hotfix` too, same as gitconfig includeIf
        # onbranch's own fnmatch-based matching.
        ("release/1.0/hotfix", ("release/*",), True),
        ("releaseX", ("release/*",), False),
        ("main", ("dev", "main"), True),  # any-of
        ("Main", ("main",), False),  # case-sensitive (fnmatchcase)
        (None, ("main",), True),  # detached HEAD: conservative default, treated as matching
        (None, (), True),
    ],
)
def test_branch_matches(branch: str | None, patterns: tuple[str, ...], expected: bool) -> None:
    assert branch_matches(branch, patterns) is expected
