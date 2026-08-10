"""Git process helpers and repository-root path safety (SPEC.md 5, 18)."""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess

from chwrite.errors import ChwriteError


def run_git(
    args: list[str], cwd: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[bytes]:
    """Run `git <args>` with argument-array (never shell) invocation.

    Args:
        args: Argument vector appended after "git".
        cwd: Working directory for the subprocess.
        check: Raise ChwriteError on a nonzero exit status.

    Returns:
        The completed process, with captured stdout/stderr.

    Raises:
        ChwriteError: git is not on PATH, or check=True and git failed.
    """
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except FileNotFoundError:
        raise ChwriteError("git executable not found on PATH", 2) from None
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        raise ChwriteError(f"git {' '.join(args)} failed: {stderr}", 2)
    return proc


def repo_root() -> str:
    """Return the realpath of the current git repository's work tree.

    Raises:
        ChwriteError: not currently inside a git work tree.
    """
    proc = run_git(["rev-parse", "--show-toplevel"])
    top = proc.stdout.decode(errors="replace").strip()
    if not top:
        raise ChwriteError("not inside a git repository", 2)
    return os.path.realpath(top)


def try_repo_root() -> str | None:
    """Like repo_root(), but returns None instead of raising.

    Used by check-path, which must not hard-error on paths outside a repo
    since an agent hook may legitimately touch files unrelated to any
    repository - only a genuinely missing git binary is an error there.
    """
    if shutil.which("git") is None:
        raise ChwriteError("git executable not found on PATH", 2)
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    top = proc.stdout.decode(errors="replace").strip()
    return os.path.realpath(top) if top else None


def strip_pathspec_magic(pattern: str) -> tuple[str, set[str]]:
    """Split git pathspec "magic" (:(glob)..., :!..., :^...) off a pattern.

    Returns:
        (bare_path, magic_words). The original pattern (with magic intact)
        is what callers hand to `git`; this is only for interpreting the
        path part locally.
    """
    if pattern.startswith(":("):
        idx = pattern.find(")")
        if idx == -1:
            raise ChwriteError(f"malformed pathspec magic: {pattern}", 2)
        magic = {w.strip() for w in pattern[2:idx].split(",") if w.strip()}
        return pattern[idx + 1 :], magic
    if pattern.startswith(":") and len(pattern) > 1 and pattern[1] in "!^":
        return pattern[2:], {"exclude"}
    return pattern, set()


def validate_pathspec(pattern: str, root: str) -> None:
    """Reject pathspecs that are absolute or resolve outside repo root."""
    bare, _magic = strip_pathspec_magic(pattern)
    if bare == "":
        raise ChwriteError(f"empty pathspec: {pattern!r}", 2)
    if bare.startswith("/") or bare.startswith("\\") or os.path.isabs(bare):
        raise ChwriteError(f"absolute paths are not allowed in pathspecs: {pattern}", 2)
    if re.match(r"^[A-Za-z]:[\\/]", bare):
        raise ChwriteError(f"absolute paths are not allowed in pathspecs: {pattern}", 2)
    parts = re.split(r"[\\/]", bare)
    if ".." in parts:
        raise ChwriteError(f"path traversal is not allowed in pathspecs: {pattern}", 2)
    candidate = os.path.normpath(os.path.join(root, bare))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise ChwriteError(f"pathspec resolves outside the repository root: {pattern}", 2)


def validate_resolved_path(rel_path: str, root: str) -> None:
    """Defense in depth against a resolved git-ls-files path escaping root.

    Git itself should never produce a path like this, but SPEC.md section
    18 requires the check regardless.
    """
    if os.path.isabs(rel_path):
        raise ChwriteError(f"refusing to protect absolute path from git: {rel_path}", 2)
    parts = re.split(r"[\\/]", rel_path)
    if ".." in parts:
        raise ChwriteError(f"refusing to protect path with '..' segment: {rel_path}", 2)
    full = os.path.normpath(os.path.join(root, rel_path))
    if full != root and not full.startswith(root + os.sep):
        raise ChwriteError(f"refusing to protect path outside repository root: {rel_path}", 2)


def check_symlink_safety(full_path: str, root: str) -> bool:
    """Return False if full_path is a symlink resolving outside repo root."""
    if os.path.islink(full_path):
        real = os.path.realpath(full_path)
        if real != root and not real.startswith(root + os.sep):
            return False
    return True


def resolve_target_path(raw_path: str, root: str) -> str | None:
    """Normalize a CWD-relative or absolute path to a repo-relative one.

    Returns:
        A posix-style repo-relative path, or None if raw_path resolves
        outside repo root (not an error - callers treat that as "not
        protected here", e.g. check-path).
    """
    p = raw_path if os.path.isabs(raw_path) else os.path.join(os.getcwd(), raw_path)
    p = os.path.normpath(p)
    real = os.path.realpath(p)
    if real != root and not real.startswith(root + os.sep):
        return None
    rel = os.path.relpath(real, root)
    if rel == os.curdir or rel.startswith(".."):
        return None
    return rel.replace(os.sep, "/")


def normalize_local_arg_path(raw: str, root: str) -> str:
    """Like resolve_target_path, but raises for explicit CLI arguments.

    "Outside the repo" is a user error for `lock <path>`/`unlock <path>`,
    not a silent no-op.
    """
    rel = resolve_target_path(raw, root)
    if rel is None:
        raise ChwriteError(f"path is outside the repository root: {raw}", 2)
    return rel


def pathspec_matches(pattern: str, rel_path: str) -> bool:
    """Lightweight standalone pathspec matcher used by check-path.

    Matches without spawning git or requiring the file to exist/be tracked,
    so it can flag a not-yet-created file inside a protected glob (e.g.
    migrations/**) before an agent creates it. apply/lock/status/verify use
    the real `git ls-files` resolution (SPEC.md section 5) instead, since
    those only ever act on files that exist and are trackable; check-path's
    job is purely advisory/fast-fail before a write is attempted, so this
    approximate matcher (covering :(glob) plus git's default literal/prefix
    semantics) is sufficient. It deliberately does not implement exotic
    pathspec magic (:(icase), :(attr:...), combined exclude sets).
    """
    bare, magic = strip_pathspec_magic(pattern)
    bare = bare.lstrip("/")
    if "glob" in magic or any(c in bare for c in "*?["):
        return fnmatch.fnmatchcase(rel_path, bare)
    return rel_path == bare or rel_path.startswith(bare + "/")
