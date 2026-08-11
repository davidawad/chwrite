#!/usr/bin/env python3
"""chwrite - GENERATED, do not hand-edit.

Regenerate with `just build` (or `python3 scripts/bundle.py`) after
changing anything under src/chwrite/. This is one of the two
single-file, stdlib-only distributable artifacts described in
SPEC.md sections 19/32; section 26 explains why the maintained
source is a package instead.

Generated from src/chwrite/ (chwrite 1.0.0).
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import json
import sys
from dataclasses import dataclass
import stat
from collections.abc import Sequence
from typing import Any, Literal, NotRequired, TypedDict
import contextlib
import getpass
import platform
from typing import NamedTuple
import argparse

# --- errors.py ---------------------------------------------------

class ChwriteError(Exception):
    """A user-facing chwrite error.

    Args:
        message: Human-readable description, written to stderr by the CLI
            entrypoint.
        code: Process exit status. Follows the exit-status convention
            documented in SPEC.md section 17 (0 valid, 1 violation, 2
            config/runtime error) unless a command defines its own
            narrower contract (e.g. check-path's --claude-hook mode).
    """

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


# --- gitutil.py --------------------------------------------------

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


# --- policy_yaml.py ----------------------------------------------

YAML_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")

_PROTECT_ITEM_KEYS = {"pattern", "regex", "message", "deny_user", "deny_group"}


def _strip_yaml_comment(line: str) -> str:
    in_single = False
    in_double = False
    for i, c in enumerate(line):
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and not in_single and not in_double and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def _yaml_unquote(value: str, filename: str, lineno: int) -> str | None:
    v = value.strip()
    if v == "":
        return None
    if v in ("---", "..."):
        raise ChwriteError(f"{filename}:{lineno}: multi-document YAML streams are not supported", 2)
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1].replace('\\"', '"')
    if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
        return v[1:-1]
    if v[0] in "[{&*|>":
        raise ChwriteError(
            f"{filename}:{lineno}: unsupported YAML construct outside chwrite's subset "
            f"(flow collections/anchors/block scalars): {v!r}",
            2,
        )
    return v


def _tokenize(text: str, filename: str) -> list[tuple[int, str, int]]:
    entries: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise ChwriteError(
                f"{filename}:{lineno}: tabs are not allowed in chwrite's YAML subset; use spaces", 2
            )
        line = _strip_yaml_comment(raw)
        if line.strip() in ("---", "..."):
            raise ChwriteError(
                f"{filename}:{lineno}: multi-document YAML streams are not supported", 2
            )
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        entries.append((indent, line.strip(), lineno))
    return entries


def _parse_protect_sequence(
    entries: list[tuple[int, str, int]], i: int, filename: str
) -> tuple[list[dict[str, str | None]], int]:
    rules: list[dict[str, str | None]] = []
    n = len(entries)
    while i < n and entries[i][0] >= 2:
        item_indent, item_content, item_lineno = entries[i]
        if item_indent != 2 or not item_content.startswith("- "):
            raise ChwriteError(
                f"{filename}:{item_lineno}: expected a '- ' sequence item under 'protect'", 2
            )
        remainder = item_content[2:].strip()
        item: dict[str, str | None] = {}
        i += 1
        if remainder:
            im = YAML_KEY_RE.match(remainder)
            if not im:
                raise ChwriteError(
                    f"{filename}:{item_lineno}: malformed sequence item: {remainder!r}", 2
                )
            item[im.group(1)] = _yaml_unquote(im.group(2), filename, item_lineno)
        while i < n and entries[i][0] == 4:
            _, key_content, key_lineno = entries[i]
            km = YAML_KEY_RE.match(key_content)
            if not km:
                raise ChwriteError(
                    f"{filename}:{key_lineno}: malformed mapping entry: {key_content!r}", 2
                )
            item[km.group(1)] = _yaml_unquote(km.group(2), filename, key_lineno)
            i += 1
        unknown = set(item.keys()) - _PROTECT_ITEM_KEYS
        if unknown:
            raise ChwriteError(
                f"{filename}:{item_lineno}: unsupported keys in chwrite's "
                f"YAML subset: {sorted(unknown)}",
                2,
            )
        if item.get("pattern") is None and item.get("regex") is None:
            raise ChwriteError(
                f"{filename}:{item_lineno}: sequence item under 'protect' "
                "must have a string 'pattern' or 'regex'",
                2,
            )
        rules.append(item)
    return rules, i


def parse_yaml_policy(text: str, filename: str) -> tuple[int, list[dict[str, str | None]]]:
    """Parse a .chwrite.yaml/.yml document into (version, raw protect items).

    Each item in the returned list is a dict with only the keys actually
    present on that sequence entry, drawn from _PROTECT_ITEM_KEYS. Callers
    (policy.py) turn these into validated Rule objects.
    """
    entries = _tokenize(text, filename)
    if not entries:
        raise ChwriteError(f"{filename}: empty policy file", 2)

    version: int | None = None
    rules: list[dict[str, str | None]] = []
    i = 0
    n = len(entries)
    while i < n:
        indent, content, lineno = entries[i]
        if indent != 0:
            raise ChwriteError(f"{filename}:{lineno}: unexpected indentation at top level", 2)
        m = YAML_KEY_RE.match(content)
        if not m:
            raise ChwriteError(
                f"{filename}:{lineno}: expected 'key: value' mapping entry, got: {content!r}", 2
            )
        key, value = m.group(1), m.group(2)
        if key == "version":
            val = _yaml_unquote(value, filename, lineno)
            try:
                version = int(val)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ChwriteError(
                    f"{filename}:{lineno}: 'version' must be an integer", 2
                ) from None
            i += 1
        elif key == "protect":
            if value.strip() not in ("", "[]"):
                raise ChwriteError(
                    f"{filename}:{lineno}: 'protect' must introduce a block sequence (or '[]')", 2
                )
            i += 1
            if value.strip() != "[]":
                new_rules, i = _parse_protect_sequence(entries, i, filename)
                rules.extend(new_rules)
        else:
            raise ChwriteError(
                f"{filename}:{lineno}: unsupported top-level key {key!r} "
                "(only 'version' and 'protect' are supported)",
                2,
            )

    if version is None:
        raise ChwriteError(f"{filename}: missing 'version'", 2)
    if version != 1:
        raise ChwriteError(
            f"{filename}: unsupported version {version} (only version 1 is supported)", 2
        )
    return version, rules


# --- policy.py ---------------------------------------------------

POLICY_FILENAMES = [".chwrite", ".chwrite.json", ".chwrite.toml", ".chwrite.yaml", ".chwrite.yml"]
ADHOC_DEFAULT_MESSAGE = "protected by chwrite (ad hoc local lock)"


@dataclass(frozen=True, slots=True)
class Rule:
    """A single `protect`/`protect-regex` rule.

    Exactly one of `pattern` (a Git pathspec, section 5) or `regex` (a
    Python `re` pattern searched against tracked paths, section 28) is
    set - never both, never neither. `deny_user`/`deny_group` narrow the
    rule to an optional, non-blanket scope (section 29); both empty means
    the default blanket-block behavior of sections 10-14.
    """

    pattern: str | None
    message: str | None = None
    regex: str | None = None
    deny_user: tuple[str, ...] = ()
    deny_group: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Policy:
    """A loaded policy file: its location, format, and rules."""

    path: str
    filename: str
    version: int
    rules: tuple[Rule, ...]


@dataclass(frozen=True, slots=True)
class ResolvedProtection:
    """What resolve_policy_files() decided for one repo-relative path."""

    message: str
    deny_user: tuple[str, ...] = ()
    deny_group: tuple[str, ...] = ()


def default_message_for(policy: Policy | None) -> str:
    """The fallback message for a rule with no explicit message (24.3)."""
    if policy is None:
        return ADHOC_DEFAULT_MESSAGE
    return f"protected by chwrite policy — see {policy.filename}"


VERSION_LINE_RE = re.compile(r"^version\s+(\d+)$")
PROTECT_LINE_RE = re.compile(r"^protect\s+(.*)$")
PROTECT_REGEX_LINE_RE = re.compile(r"^protect-regex\s+(.*)$")

# A single trailing `key="quoted value"` or `key=bareword` option, anchored
# to the end of the line. Repeatedly stripping matches of this from the end
# lets message=/deny-user=/deny-group= appear in any order/combination
# after the pathspec or regex body, same as the original message-only
# grammar (section 24.1) extended for section 29's scope options.
_TRAILING_OPTION_RE = re.compile(
    r"^(?P<rest>.*)\s+(?P<key>message|deny-user|deny-group)="
    r'(?P<value>"(?:[^"\\]|\\.)*"|\S+)$'
)
_KNOWN_OPTIONS = {"message", "deny-user", "deny-group"}


def _strip_trailing_options(text: str) -> tuple[str, dict[str, str]]:
    """Peel `key=value`/`key="quoted"` tokens off the end of `text`.

    Returns (remaining_body, {key: raw_value_with_quotes_stripped}). The
    rightmost occurrence of a given key wins if it somehow appears twice
    (not expected, but not ambiguous either).
    """
    options: dict[str, str] = {}
    while True:
        m = _TRAILING_OPTION_RE.match(text)
        if not m:
            break
        key = m.group("key")
        raw_value = m.group("value")
        if raw_value.startswith('"') and raw_value.endswith('"'):
            value = raw_value[1:-1].replace('\\"', '"')
        else:
            value = raw_value
        options.setdefault(key, value)
        text = m.group("rest")
    return text.strip(), options


def _parse_scope_value(raw: str | None, key: str, filename: str, lineno: int) -> tuple[str, ...]:
    """Split a comma-separated deny-user=/deny-group= value into names."""
    if raw is None:
        return ()
    names = tuple(n.strip() for n in raw.split(",") if n.strip())
    if not names:
        raise ChwriteError(f"{filename}:{lineno}: {key}= requires at least one name", 2)
    return names


def _build_rule(
    filename: str,
    lineno: int,
    *,
    pattern: str | None,
    regex: str | None,
    options: dict[str, str],
) -> Rule:
    message = options.get("message")
    deny_user = _parse_scope_value(options.get("deny-user"), "deny-user", filename, lineno)
    deny_group = _parse_scope_value(options.get("deny-group"), "deny-group", filename, lineno)
    return Rule(pattern, message, regex, deny_user, deny_group)


def _parse_plain(text: str, filename: str) -> tuple[int, list[Rule]]:
    version = None
    rules: list[Rule] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("version"):
            m = VERSION_LINE_RE.match(line)
            if not m:
                raise ChwriteError(f"{filename}:{lineno}: malformed version line: {line!r}", 2)
            version = int(m.group(1))
            continue
        if line.startswith("protect-regex"):
            m = PROTECT_REGEX_LINE_RE.match(line)
            if not m or not m.group(1).strip():
                raise ChwriteError(
                    f"{filename}:{lineno}: malformed protect-regex line: {line!r}", 2
                )
            core, options = _strip_trailing_options(m.group(1))
            if not core:
                raise ChwriteError(
                    f"{filename}:{lineno}: protect-regex requires a pattern: {line!r}", 2
                )
            rules.append(_build_rule(filename, lineno, pattern=None, regex=core, options=options))
            continue
        if line.startswith("protect"):
            m = PROTECT_LINE_RE.match(line)
            if not m or not m.group(1).strip():
                raise ChwriteError(f"{filename}:{lineno}: malformed protect line: {line!r}", 2)
            core, options = _strip_trailing_options(m.group(1))
            if not core:
                raise ChwriteError(f"{filename}:{lineno}: malformed protect line: {line!r}", 2)
            rules.append(_build_rule(filename, lineno, pattern=core, regex=None, options=options))
            continue
        raise ChwriteError(f"{filename}:{lineno}: unrecognized line: {line!r}", 2)
    if version is None:
        raise ChwriteError(f"{filename}: missing 'version 1' declaration", 2)
    if version != 1:
        raise ChwriteError(
            f"{filename}: unsupported version {version} (only version 1 is supported)", 2
        )
    return version, rules


def _scope_from_structured(value: object, key: str, filename: str, idx: int) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ChwriteError(f"{filename}: protect[{idx}].{key} must be a list of strings", 2)
    value_list: list[object] = value  # pyright: ignore[reportUnknownVariableType]
    names: list[str] = []
    for item in value_list:
        if not isinstance(item, str):
            raise ChwriteError(f"{filename}: protect[{idx}].{key} must be a list of strings", 2)
        stripped = item.strip()
        if stripped:
            names.append(stripped)
    if not names:
        raise ChwriteError(f"{filename}: protect[{idx}].{key} must not be empty", 2)
    return tuple(names)


def _parse_structured(data: object, filename: str) -> tuple[int, list[Rule]]:
    # This function's whole job is giving structural (isinstance) shape to
    # untyped external JSON/TOML data without a schema-validation library
    # (pydantic is exactly the tool for this, but section 1's "no external
    # libraries at runtime" rule for the distributed chwrite.py rules it
    # out here) - pyright strict still reports residual "partially
    # unknown" noise a few lines below even after each isinstance/cast,
    # which is expected and accepted at this specific untyped-data
    # boundary, not elsewhere in the codebase.
    if not isinstance(data, dict):
        raise ChwriteError(f"{filename}: top-level value must be a mapping/object", 2)
    mapping: dict[str, object] = data  # pyright: ignore[reportUnknownVariableType]
    version: object = mapping.get("version")
    if version is None:
        raise ChwriteError(f"{filename}: missing 'version'", 2)
    if version != 1:
        raise ChwriteError(
            f"{filename}: unsupported version {version!r} (only version 1 is supported)", 2
        )
    protect: object = mapping.get("protect", [])
    if not isinstance(protect, list):
        raise ChwriteError(f"{filename}: 'protect' must be a list", 2)
    protect_list: list[object] = protect  # pyright: ignore[reportUnknownVariableType]
    rules: list[Rule] = []
    for i, item in enumerate(protect_list):
        if not isinstance(item, dict):
            raise ChwriteError(f"{filename}: protect[{i}] must be an object", 2)
        item_map: dict[str, object] = item  # pyright: ignore[reportUnknownVariableType]
        pattern: object = item_map.get("pattern")
        regex: object = item_map.get("regex")
        if (pattern is None) == (regex is None):
            raise ChwriteError(
                f"{filename}: protect[{i}] must have exactly one of 'pattern' or 'regex'", 2
            )
        if pattern is not None and not isinstance(pattern, str):
            raise ChwriteError(f"{filename}: protect[{i}].pattern must be a string", 2)
        if regex is not None and not isinstance(regex, str):
            raise ChwriteError(f"{filename}: protect[{i}].regex must be a string", 2)
        message: object = item_map.get("message")
        if message is not None and not isinstance(message, str):
            raise ChwriteError(f"{filename}: protect[{i}].message must be a string", 2)
        deny_user = _scope_from_structured(item_map.get("deny_user"), "deny_user", filename, i)
        deny_group = _scope_from_structured(item_map.get("deny_group"), "deny_group", filename, i)
        rules.append(Rule(pattern, message, regex, deny_user, deny_group))  # pyright: ignore[reportUnknownArgumentType]
    assert isinstance(version, int)
    return version, rules


def _parse_json(text: str, filename: str) -> tuple[int, list[Rule]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ChwriteError(f"{filename}: invalid JSON: {e}", 2) from e
    return _parse_structured(data, filename)


def _parse_toml(path: str, filename: str) -> tuple[int, list[Rule]]:
    # This repo's own dev tooling targets 3.13+, but the *distributed*
    # chwrite.py (SPEC.md section 19) supports Python 3.11+ at runtime, so
    # this guard is not dead code even though ruff's UP036 (evaluated
    # against this repo's target-version) thinks it is.
    if sys.version_info < (3, 11):  # noqa: UP036
        raise ChwriteError(
            f"{filename}: TOML policy files require Python 3.11+ (tomllib is not available on this "
            "interpreter); use .chwrite, .chwrite.json, or .chwrite.yaml instead",
            2,
        )
    # Deferred so environments below 3.11 - which will never reach this
    # line thanks to the guard above - don't need tomllib to exist just to
    # import this module (chwrite works fine there for the other three
    # policy formats).
    import tomllib  # noqa: PLC0415

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ChwriteError(f"{filename}: invalid TOML: {e}", 2) from e
    return _parse_structured(data, filename)


def _build_rule_from_yaml_item(item: dict[str, str | None], filename: str, idx: int) -> Rule:
    unknown = set(item.keys()) - {"pattern", "regex", "message", "deny_user", "deny_group"}
    if unknown:
        raise ChwriteError(
            f"{filename}: protect[{idx}] has unsupported keys in chwrite's YAML subset: "
            f"{sorted(unknown)}",
            2,
        )
    pattern = item.get("pattern")
    regex = item.get("regex")
    if (pattern is None) == (regex is None):
        raise ChwriteError(
            f"{filename}: protect[{idx}] must have exactly one of 'pattern' or 'regex'", 2
        )
    message = item.get("message")
    # The chwrite YAML subset has no flow-sequence support (policy_yaml.py
    # module docstring), so deny_user/deny_group are written the same way
    # as the plain format: a single comma-separated scalar, not a list.
    deny_user = _parse_scope_value(item.get("deny_user"), "deny_user", filename, idx)
    deny_group = _parse_scope_value(item.get("deny_group"), "deny_group", filename, idx)
    return Rule(pattern, message, regex, deny_user, deny_group)


def find_policy_file(root: str) -> str | None:
    """Return the one present policy filename at repo root, or None.

    Raises:
        ChwriteError: more than one policy file is present (ambiguous).
    """
    present = [f for f in POLICY_FILENAMES if os.path.isfile(os.path.join(root, f))]
    if len(present) > 1:
        raise ChwriteError(
            "multiple chwrite policy files found at repository root: "
            + ", ".join(present)
            + " (exactly one is allowed - see SPEC.md section 24)",
            2,
        )
    return present[0] if present else None


def _validate_rule(rule: Rule, filename: str, root: str) -> None:
    if rule.pattern is not None:
        validate_pathspec(rule.pattern, root)
        return
    assert rule.regex is not None
    try:
        re.compile(rule.regex)
    except re.error as e:
        raise ChwriteError(f"{filename}: invalid regex {rule.regex!r}: {e}", 2) from e


def load_policy(root: str) -> Policy | None:
    """Load and validate the repo's policy file, if any."""
    filename = find_policy_file(root)
    if filename is None:
        return None
    path = os.path.join(root, filename)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if filename == ".chwrite":
        version, rules = _parse_plain(text, filename)
    elif filename == ".chwrite.json":
        version, rules = _parse_json(text, filename)
    elif filename == ".chwrite.toml":
        version, rules = _parse_toml(path, filename)
    else:
        yaml_version, yaml_items = parse_yaml_policy(text, filename)
        rules = [
            _build_rule_from_yaml_item(item, filename, idx) for idx, item in enumerate(yaml_items)
        ]
        version = yaml_version
    for r in rules:
        _validate_rule(r, filename, root)
    return Policy(path, filename, version, tuple(rules))


def resolve_policy_files(root: str, policy: Policy | None) -> dict[str, ResolvedProtection]:
    """Resolve every rule (pathspec or regex) via `git ls-files` (SPEC.md 5, 28).

    Pathspec rules resolve via `git ls-files -z -- <pathspec>`. Regex rules
    never resolve paths themselves - they `re.search()` against the same
    `git ls-files -z` (whole-repo) candidate list pathspec rules use, so
    the path-traversal/symlink-escape protections in section 18 apply
    identically either way (section 28).

    Returns:
        {repo_relative_path: ResolvedProtection}. A file matched by more
        than one rule is protected exactly once; the *last*-defined
        matching rule wins for message/scope purposes (policy file order,
        section 28's documented precedence).
    """
    mapping: dict[str, ResolvedProtection] = {}
    if policy is None:
        return mapping
    all_files: list[str] | None = None
    for rule in policy.rules:
        message = rule.message if rule.message else default_message_for(policy)
        resolved = ResolvedProtection(message, rule.deny_user, rule.deny_group)
        if rule.pattern is not None:
            proc = run_git(["ls-files", "-z", "--", rule.pattern], cwd=root)
            raw = proc.stdout.decode("utf-8", errors="surrogateescape")
            matches = list(filter(None, raw.split("\x00")))
        else:
            assert rule.regex is not None
            if all_files is None:
                proc = run_git(["ls-files", "-z"], cwd=root)
                raw = proc.stdout.decode("utf-8", errors="surrogateescape")
                all_files = list(filter(None, raw.split("\x00")))
            try:
                compiled = re.compile(rule.regex)
            except re.error as e:
                raise ChwriteError(
                    f"{policy.filename}: invalid regex {rule.regex!r}: {e}", 2
                ) from e
            matches = [f for f in all_files if compiled.search(f.replace(os.sep, "/"))]
        for rel in matches:
            validate_resolved_path(rel, root)
            mapping[rel] = resolved
    return mapping


def _write_text(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)


def _toml_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_plain(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to the plain `.chwrite` format."""
    lines = [f"version {version}", ""]
    for r in rules:
        keyword = "protect-regex" if r.regex is not None else "protect"
        body = r.regex if r.regex is not None else r.pattern
        line = f"{keyword} {body}"
        if r.message:
            line += f' message="{r.message.replace(chr(34), chr(92) + chr(34))}"'
        if r.deny_user:
            line += f" deny-user={','.join(r.deny_user)}"
        if r.deny_group:
            line += f" deny-group={','.join(r.deny_group)}"
        lines.append(line)
    _write_text(path, "\n".join(lines) + "\n")


def write_json(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to `.chwrite.json`."""
    protect: list[dict[str, object]] = []
    for r in rules:
        item: dict[str, object] = {}
        if r.regex is not None:
            item["regex"] = r.regex
        else:
            item["pattern"] = r.pattern
        if r.message:
            item["message"] = r.message
        if r.deny_user:
            item["deny_user"] = list(r.deny_user)
        if r.deny_group:
            item["deny_group"] = list(r.deny_group)
        protect.append(item)
    _write_text(path, json.dumps({"version": version, "protect": protect}, indent=2) + "\n")


def write_toml(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to `.chwrite.toml`."""
    lines = [f"version = {version}"]
    for r in rules:
        lines.append("")
        lines.append("[[protect]]")
        if r.regex is not None:
            lines.append(f"regex = {_toml_quote(r.regex)}")
        else:
            assert r.pattern is not None
            lines.append(f"pattern = {_toml_quote(r.pattern)}")
        if r.message:
            lines.append(f"message = {_toml_quote(r.message)}")
        if r.deny_user:
            lines.append("deny_user = [" + ", ".join(_toml_quote(n) for n in r.deny_user) + "]")
        if r.deny_group:
            lines.append("deny_group = [" + ", ".join(_toml_quote(n) for n in r.deny_group) + "]")
    _write_text(path, "\n".join(lines) + "\n")


def write_yaml(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to `.chwrite.yaml`."""
    lines = [f"version: {version}"]
    if not rules:
        lines.append("protect: []")
    else:
        lines.append("protect:")
        for r in rules:
            if r.regex is not None:
                lines.append(f"  - regex: {_toml_quote(r.regex)}")
            else:
                assert r.pattern is not None
                lines.append(f"  - pattern: {_toml_quote(r.pattern)}")
            if r.message:
                lines.append(f"    message: {_toml_quote(r.message)}")
            if r.deny_user:
                lines.append(f"    deny_user: {_toml_quote(','.join(r.deny_user))}")
            if r.deny_group:
                lines.append(f"    deny_group: {_toml_quote(','.join(r.deny_group))}")
    _write_text(path, "\n".join(lines) + "\n")


POLICY_WRITERS = {
    ".chwrite": write_plain,
    ".chwrite.json": write_json,
    ".chwrite.toml": write_toml,
    ".chwrite.yaml": write_yaml,
    ".chwrite.yml": write_yaml,
}


# --- state.py ----------------------------------------------------

STATE_VERSION = 1


class ScopeDict(TypedDict):
    """A narrowed deny-user/deny-group restriction (SPEC.md section 29)."""

    deny_user: list[str]
    deny_group: list[str]


# "all" is the default blanket-block mode (sections 10-14, unchanged behavior).
# A ScopeDict means the protection only denies the named identities.
Scope = Literal["all"] | ScopeDict


def make_scope(deny_user: Sequence[str], deny_group: Sequence[str]) -> Scope:
    """Build the state.json scope value for a set of deny-user/deny-group names."""
    if not deny_user and not deny_group:
        return "all"
    return {"deny_user": list(deny_user), "deny_group": list(deny_group)}


def scope_deny_user(scope: Scope) -> list[str]:
    """The deny-user names for a scope, or [] for blanket/absent scope."""
    if scope == "all":
        return []
    return scope.get("deny_user", [])


def scope_deny_group(scope: Scope) -> list[str]:
    """The deny-group names for a scope, or [] for blanket/absent scope."""
    if scope == "all":
        return []
    return scope.get("deny_group", [])


class ProtectResult(TypedDict):
    """What a backend's protect_*() returns: just what it actually did.

    Deliberately smaller than FileEntry - a backend has no opinion on
    source/message/original_mode, which are the caller's bookkeeping.
    """

    backend: str
    level: str
    hard: bool
    acl_user: NotRequired[str]
    # The exact ACE/ACL descriptor(s) applied by a *scoped* backend (macOS
    # `chmod +a` argument, Linux `u:<name>`/`g:<name>` setfacl targets,
    # Windows icacls names) - recorded so unprotect can remove precisely
    # what was added, per identity (SPEC.md section 29).
    acl_entries: NotRequired[list[str]]


class FileEntry(TypedDict):
    """One protected-file record inside state.json."""

    backend: str
    level: str
    original_mode: int | None
    locked: bool
    source: str  # "policy" | "adhoc"
    message: str
    hard: bool
    acl_user: NotRequired[str]
    acl_entries: NotRequired[list[str]]
    # Absent/"all" = blanket block (sections 10-14). A ScopeDict means this
    # file is only denied to specific users/groups (section 29). status/
    # doctor treat this as advisory bookkeeping only - they re-derive the
    # real state from the OS, same as everywhere else in this module.
    scope: NotRequired[Scope]


class StateDoc(TypedDict):
    """The full contents of state.json."""

    version: int
    files: dict[str, FileEntry]


def state_paths(root: str) -> tuple[str, str]:
    """Return (state_dir, state_file) for the repo at root.

    Resolved via `git rev-parse --git-dir` (not a hardcoded ".git/") so
    this is correct for worktrees and repos with a non-default git dir.
    """
    proc = run_git(["rev-parse", "--git-dir"], cwd=root)
    git_dir = proc.stdout.decode(errors="replace").strip()
    if not os.path.isabs(git_dir):
        git_dir = os.path.join(root, git_dir)
    git_dir = os.path.realpath(git_dir)
    state_dir = os.path.join(git_dir, "chwrite")
    return state_dir, os.path.join(state_dir, "state.json")


def load_state(root: str) -> StateDoc:
    """Load state.json, or an empty document if it does not exist yet."""
    _, state_file = state_paths(root)
    if not os.path.isfile(state_file):
        return {"version": STATE_VERSION, "files": {}}
    try:
        with open(state_file, encoding="utf-8") as f:
            data: Any = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        raise ChwriteError(f"corrupt chwrite state file {state_file}: {e}", 2) from e
    if not isinstance(data, dict) or "files" not in data or not isinstance(data["files"], dict):
        raise ChwriteError(f"corrupt chwrite state file {state_file}: missing 'files'", 2)
    return data  # type: ignore[no-any-return]


def save_state(root: str, data: StateDoc) -> None:
    """Atomically write state.json (write to temp file, then rename)."""
    state_dir, state_file = state_paths(root)
    os.makedirs(state_dir, exist_ok=True)
    tmp = state_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, state_file)


def determine_original_mode(full_path: str, entry: FileEntry | None) -> int | None:
    """The POSIX mode to restore on unlock: reuse a stored value if present.

    Only reads the file's *current* mode when no prior value is recorded,
    since re-deriving it from an already-protected file would capture the
    protected (e.g. read-only) mode instead of the true original one.
    """
    if entry and entry.get("original_mode") is not None:
        return entry["original_mode"]
    try:
        return stat.S_IMODE(os.lstat(full_path).st_mode)
    except FileNotFoundError:
        return None


# --- backends/posix_generic.py -----------------------------------

def chmod_readonly(full_path: str) -> None:
    """Strip write bits while preserving read/execute bits."""
    mode = stat.S_IMODE(os.lstat(full_path).st_mode)
    os.chmod(full_path, mode & ~0o222)


def protect_posix(full_path: str, hard: bool) -> ProtectResult:
    """Apply chmod a-w. Classified READONLY (SPEC.md section 13)."""
    if hard:
        sys.stderr.write(
            "note: HARD protection is only implemented for Linux (chattr) in this version; "
            "applying READONLY instead.\n"
        )
    chmod_readonly(full_path)
    return {"backend": "posix-chmod", "level": "READONLY", "hard": False}


def unprotect_posix(full_path: str, entry: FileEntry) -> None:
    """Restore the recorded original mode."""
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        os.chmod(full_path, original_mode)


def query_posix(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state - never trust state.json alone."""
    try:
        st = os.lstat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "posix-chmod"
    return "UNPROTECTED", None


# --- backends/macos.py -------------------------------------------

def protect_macos(full_path: str, hard: bool) -> ProtectResult:
    """Apply chflags uchg (ENFORCED), falling back to chmod (READONLY)."""
    if hard:
        sys.stderr.write(
            "note: HARD protection (schg) is not implemented on macOS by chwrite "
            "(see SPEC.md section 10); applying ENFORCED (uchg) instead.\n"
        )
    if shutil.which("chflags"):
        proc = subprocess.run(["chflags", "uchg", full_path], capture_output=True, check=False)
        if proc.returncode == 0:
            return {"backend": "macos-uchg", "level": "ENFORCED", "hard": False}
    chmod_readonly(full_path)
    return {"backend": "macos-chmod", "level": "READONLY", "hard": False}


def unprotect_macos(full_path: str, entry: FileEntry) -> None:
    """Reverse protect_macos, restoring the recorded original mode."""
    if entry.get("backend") == "macos-uchg" and shutil.which("chflags"):
        # nouchg must run before chmod: the BSD immutable flag also blocks
        # ordinary chmod() calls while set.
        subprocess.run(["chflags", "nouchg", full_path], capture_output=True, check=False)
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        os.chmod(full_path, original_mode)


def query_macos(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state - never trust state.json alone."""
    try:
        st = os.lstat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    uf_immutable = getattr(stat, "UF_IMMUTABLE", 0x00000002)
    flags = getattr(st, "st_flags", 0)
    if flags & uf_immutable:
        return "ENFORCED", "macos-uchg"
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "macos-chmod"
    return "UNPROTECTED", None


# --- Scoped (deny-user/deny-group) backend: SPEC.md section 29 ------------
#
# APFS/HFS+ support NFSv4-style ACL entries via `chmod +a`/`chmod -a`,
# which - unlike the blanket `uchg` flag above - support an explicit
# *deny* clause for a named user or group. This is a genuinely different
# mechanism from protect_macos()'s file-flag approach, selected only when
# a rule/lock carries a deny-user=/deny-group= scope. Classification is
# ENFORCED (the file owner can still edit its own ACL - same caveat as
# the blanket uchg backend and as Windows' icacls deny ACE).

_DENY_RIGHTS = "deny write,delete,append,writeattr,chown"


def _macos_ace(kind: str, name: str) -> str:
    return f"{kind}:{name} {_DENY_RIGHTS}"


def _macos_chmod_path() -> str | None:
    """Prefer the system `/bin/chmod` over whatever `chmod` resolves to on
    PATH. macOS's own chmod is the only one that understands `+a`/`-a`
    NFSv4-style ACL syntax; a GNU coreutils `chmod` earlier on PATH (common
    with Homebrew's `coreutils` formula, or this project's own Nix dev
    environment) silently rejects `+a` as "invalid mode" instead - falling
    back to shutil.which("chmod") here would risk shelling out to the
    wrong binary entirely, which is exactly the kind of silent-wrong-thing
    SPEC.md 29.1 says never to do."""
    if os.path.exists("/bin/chmod"):
        return "/bin/chmod"
    return shutil.which("chmod")


def protect_macos_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply one `chmod +a` deny ACE per named user/group. Never falls back:
    if this fails, the caller learns exactly what's missing (SPEC.md 29.1's
    "never silently fall back to blanket-block or silently no-op")."""
    chmod = _macos_chmod_path()
    if not chmod:
        raise ChwriteError("chmod not found; cannot apply a macOS ACL deny entry", 2)
    applied: list[str] = []
    for name in deny_user:
        ace = _macos_ace("user", name)
        proc = subprocess.run([chmod, "+a", ace, full_path], capture_output=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"chmod +a failed to deny write access to user {name!r} on {full_path}: {stderr}",
                2,
            )
        applied.append(ace)
    for name in deny_group:
        ace = _macos_ace("group", name)
        proc = subprocess.run([chmod, "+a", ace, full_path], capture_output=True, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"chmod +a failed to deny write access to group {name!r} on {full_path}: "
                f"{stderr} (chwrite never creates/modifies groups - {name!r} must already exist)",
                2,
            )
        applied.append(ace)
    return {"backend": "macos-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": applied}


def unprotect_macos_scoped(full_path: str, entry: FileEntry) -> None:
    """Remove exactly the ACEs protect_macos_scoped() added, in reverse order."""
    chmod = _macos_chmod_path()
    if not chmod or not os.path.exists(full_path):
        return
    for ace in reversed(entry.get("acl_entries", [])):
        subprocess.run([chmod, "-a", ace, full_path], capture_output=True, check=False)


def _macos_ls_path() -> str:
    """Same PATH-shadowing concern as _macos_chmod_path(): GNU coreutils
    `ls` has no `-e` (ACL display) flag at all, so it must be the real
    macOS `/bin/ls`, not whatever `ls` resolves to first on PATH."""
    if os.path.exists("/bin/ls"):
        return "/bin/ls"
    return shutil.which("ls") or "ls"


def query_macos_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> tuple[str, str | None]:
    """Inspect the real ACL (`ls -le`) for the specific deny entries requested -
    never trusts state.json alone, same principle as query_macos()."""
    if not os.path.exists(full_path):
        return "MISSING", None
    if not (deny_user or deny_group):
        return "UNPROTECTED", None
    proc = subprocess.run([_macos_ls_path(), "-le", full_path], capture_output=True, check=False)
    if proc.returncode != 0:
        return "UNPROTECTED", None
    out = proc.stdout.decode(errors="replace")
    for name in deny_user:
        if f"user:{name} deny" not in out:
            return "UNPROTECTED", None
    for name in deny_group:
        if f"group:{name} deny" not in out:
            return "UNPROTECTED", None
    return "ENFORCED", "macos-acl-deny"


# --- backends/linux.py -------------------------------------------

def protect_linux(full_path: str, hard: bool) -> ProtectResult:
    """Apply chmod a-w (READONLY) by default, or chattr +i (HARD) via --hard.

    Never invokes sudo: if chattr fails for lack of privilege, prints the
    command the user must run themselves (SPEC.md section 11) and falls
    back to READONLY rather than silently doing nothing.
    """
    if hard:
        if shutil.which("chattr"):
            proc = subprocess.run(["chattr", "+i", full_path], capture_output=True, check=False)
            if proc.returncode == 0:
                return {"backend": "linux-chattr", "level": "HARD", "hard": True}
            stderr = proc.stderr.decode(errors="replace").strip()
            sys.stderr.write(
                "Hard protection requires CAP_LINUX_IMMUTABLE.\n\n"
                "Run:\n\n"
                "    sudo chwrite lock --hard\n\n"
                f"(chattr reported: {stderr})\n"
            )
        else:
            sys.stderr.write(
                "chattr not found; cannot apply HARD protection. Falling back to READONLY.\n"
            )
    chmod_readonly(full_path)
    return {"backend": "linux-chmod", "level": "READONLY", "hard": False}


def unprotect_linux(full_path: str, entry: FileEntry) -> None:
    """Reverse protect_linux. Never invokes sudo to clear chattr +i."""
    if entry.get("backend") == "linux-chattr":
        if shutil.which("chattr"):
            proc = subprocess.run(["chattr", "-i", full_path], capture_output=True, check=False)
            if proc.returncode != 0:
                stderr = proc.stderr.decode(errors="replace").strip()
                raise ChwriteError(
                    f"failed to clear immutable flag on {full_path}: {stderr}\n"
                    f"Run:\n\n    sudo chattr -i {full_path}",
                    2,
                )
        else:
            raise ChwriteError(f"chattr not found; cannot clear immutable flag on {full_path}", 2)
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        os.chmod(full_path, original_mode)


def query_linux(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state via lsattr/mode bits - never trust state.json."""
    try:
        st = os.lstat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    if shutil.which("lsattr"):
        proc = subprocess.run(["lsattr", "-d", full_path], capture_output=True, check=False)
        if proc.returncode == 0:
            out = proc.stdout.decode(errors="replace").strip()
            fields = out.split(None, 1)
            if fields and "i" in fields[0]:
                return "HARD", "linux-chattr"
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "linux-chmod"
    return "UNPROTECTED", None


# --- Scoped (deny-user/deny-group) backend: SPEC.md section 29 ------------
#
# POSIX.1e ACLs (what setfacl/getfacl manage) have NO deny entry type -
# only allow entries plus the base owner/group/other bits. The
# access-check algorithm resolves a non-owner user's access via the FIRST
# matching category (named-user entry beats group entries beats "other"),
# so `setfacl -m u:<name>:0 <file>` reliably blocks that specific
# non-owner user regardless of group/other bits. deny-group is weaker:
# POSIX ACL group entries are ADDITIVE, so if the denied group is one of
# several groups granting that user access, the union still grants write
# (SPEC.md 29.1) - chwrite documents this in status/doctor output
# (see cli.py's LINUX_DENY_GROUP_CAVEAT), it does not pretend otherwise.
#
# chwrite MUST NOT create/modify system users or groups, and must refuse
# (not silently no-op or fall back to blanket-block) if setfacl/getfacl or
# ACL filesystem support are unavailable.


def _acl_tools_available() -> bool:
    return shutil.which("setfacl") is not None and shutil.which("getfacl") is not None


def linux_acl_capability() -> str:
    """Human-readable ACL-tooling availability for `chwrite doctor` (29.1)."""
    if _acl_tools_available():
        return "available (setfacl/getfacl found on PATH)"
    return (
        "NOT available - install the 'acl' package (e.g. `apt install acl` / "
        "`dnf install acl`) to use deny-user/deny-group scoped locks"
    )


def protect_linux_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply one `setfacl -m u:<name>:0`/`g:<name>:0` deny-equivalent entry
    per named user/group. Refuses (never falls back or no-ops) if setfacl/
    getfacl are missing or the filesystem rejects the ACL (no ACL mount
    support, or - for deny_group - a nonexistent group name)."""
    if not _acl_tools_available():
        raise ChwriteError(
            "deny-user/deny-group scoped locks require the 'acl' package (setfacl and getfacl) "
            "on Linux, which was not found on PATH. Install it, or use an unscoped `protect` "
            "rule instead (SPEC.md section 29).",
            2,
        )
    applied: list[str] = []
    for name in deny_user:
        proc = subprocess.run(
            ["setfacl", "-m", f"u:{name}:0", full_path], capture_output=True, check=False
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"setfacl failed to deny user {name!r} on {full_path}: {stderr} "
                "(the filesystem may not be mounted with ACL support - see SPEC.md section 29)",
                2,
            )
        applied.append(f"u:{name}")
    for name in deny_group:
        proc = subprocess.run(
            ["setfacl", "-m", f"g:{name}:0", full_path], capture_output=True, check=False
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"setfacl failed to deny group {name!r} on {full_path}: {stderr} "
                f"(the group {name!r} may not exist - chwrite never creates groups - or the "
                "filesystem may not be mounted with ACL support; see SPEC.md section 29)",
                2,
            )
        applied.append(f"g:{name}")
    return {"backend": "linux-acl-deny", "level": "ENFORCED", "hard": False, "acl_entries": applied}


def unprotect_linux_scoped(full_path: str, entry: FileEntry) -> None:
    """Remove exactly the ACL entries protect_linux_scoped() added."""
    if not shutil.which("setfacl") or not os.path.exists(full_path):
        return
    for spec in entry.get("acl_entries", []):
        kind, _, name = spec.partition(":")
        subprocess.run(
            ["setfacl", "-x", f"{kind}:{name}", full_path], capture_output=True, check=False
        )


def _getfacl_output(full_path: str) -> str | None:
    if not shutil.which("getfacl"):
        return None
    proc = subprocess.run(["getfacl", "-p", full_path], capture_output=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace")


def query_linux_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> tuple[str, str | None]:
    """Inspect the real ACL (`getfacl`) for the specific deny entries requested."""
    if not os.path.exists(full_path):
        return "MISSING", None
    if not (deny_user or deny_group):
        return "UNPROTECTED", None
    out = _getfacl_output(full_path)
    if out is None:
        return "UNPROTECTED", None
    all_denied = all(f"user:{n}:---" in out for n in deny_user) and all(
        f"group:{n}:---" in out for n in deny_group
    )
    if all_denied:
        return "ENFORCED", "linux-acl-deny"
    return "UNPROTECTED", None


# --- backends/windows.py -----------------------------------------

def _windows_username() -> str:
    return os.environ.get("USERNAME") or getpass.getuser()


def _icacls_path() -> str | None:
    return shutil.which("icacls") or shutil.which("icacls.exe")


def protect_windows(full_path: str, hard: bool) -> ProtectResult:
    """Apply an icacls deny ACE (ENFORCED), or FILE_ATTRIBUTE_READONLY."""
    if hard:
        sys.stderr.write(
            "note: HARD protection is not available on Windows (see SPEC.md section 12); "
            "applying ENFORCED (icacls deny ACE) instead.\n"
        )
    icacls = _icacls_path()
    if icacls:
        user = _windows_username()
        # We remove exactly the deny ACE we add (icacls /remove:d) on
        # unlock rather than snapshot/restoring the whole DACL via
        # `icacls /save`/`/restore`: that round-trip uses a binary format
        # we cannot validate without a Windows machine to test against,
        # and a naive restore risks clobbering ACL entries another tool
        # added in the meantime. Removing only our own explicit deny
        # entry is safer and simpler, at the cost of not being a
        # byte-for-byte ACL restore.
        #
        # Granular rights (WD,AD,WEA,WA), NOT the simple "(W)" alias:
        # confirmed on real Windows CI that `/deny user:(W)` denies READS
        # too, not just writes - icacls's simple "W" write alias silently
        # includes DELETE, and denying DELETE alone (independent of any
        # read-related bit) blocks ordinary file reads on Windows for
        # reasons that don't reduce to documented NTFS generic-rights
        # mappings. (WD,AD,WEA,WA) - write data/append data/write
        # extended attributes/write attributes - was verified bit-by-bit
        # to block write+append while leaving reads unaffected. Omitting
        # DELETE here doesn't weaken the documented guarantee: section 12
        # already says Windows can't reliably block delete/rename via a
        # file-level ACE alone, since the parent directory's
        # FILE_DELETE_CHILD right can permit it regardless.
        proc = subprocess.run(
            [icacls, full_path, "/deny", f"{user}:(WD,AD,WEA,WA)"], capture_output=True, check=False
        )
        if proc.returncode == 0:
            return {
                "backend": "windows-acl",
                "level": "ENFORCED",
                "hard": False,
                "acl_user": user,
            }
    with contextlib.suppress(OSError):
        os.chmod(full_path, stat.S_IREAD)
    return {"backend": "windows-readonly", "level": "READONLY", "hard": False}


def unprotect_windows(full_path: str, entry: FileEntry) -> None:
    """Remove the deny ACE we added, or restore the recorded mode."""
    if entry.get("backend") == "windows-acl":
        icacls = _icacls_path()
        user = entry.get("acl_user") or _windows_username()
        if icacls:
            subprocess.run([icacls, full_path, "/remove:d", user], capture_output=True, check=False)
    original_mode = entry.get("original_mode")
    if original_mode is not None and os.path.exists(full_path):
        with contextlib.suppress(OSError):
            os.chmod(full_path, original_mode)


def query_windows(full_path: str) -> tuple[str, str | None]:
    """Inspect real on-disk state - never trust state.json alone.

    Best-effort: icacls output is localized on non-English Windows, so the
    DENY substring check is not fully robust. It is only used to detect
    flag *removal* for status/verify; the authoritative record of what
    chwrite applied lives in state.json.
    """
    try:
        st = os.stat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    icacls = _icacls_path()
    if icacls:
        proc = subprocess.run([icacls, full_path], capture_output=True, check=False)
        if proc.returncode == 0 and "DENY" in proc.stdout.decode(errors="replace").upper():
            return "ENFORCED", "windows-acl"
    attrs = getattr(st, "st_file_attributes", None)
    if attrs is not None and attrs & 0x1:  # FILE_ATTRIBUTE_READONLY
        return "READONLY", "windows-readonly"
    mode = stat.S_IMODE(st.st_mode)
    if not (mode & 0o200):
        return "READONLY", "windows-readonly"
    return "UNPROTECTED", None


# --- Scoped (deny-user/deny-group) backend: SPEC.md section 29 ------------
#
# icacls already supports an explicit deny ACE (protect_windows() above);
# this generalizes the target from "current user's SID" to an arbitrary
# named user or group's SID. icacls's `/deny "<name>:(W)"` syntax accepts
# either a user or a group name identically, so one implementation serves
# both. Classification stays ENFORCED (the object owner can still edit its
# own DACL - same caveat as section 12).


def protect_windows_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply one icacls deny ACE per named user/group. Never falls back to
    FILE_ATTRIBUTE_READONLY (that would misrepresent who is denied) - if
    icacls is unavailable, or a name can't be resolved to a SID, this
    raises rather than silently doing something weaker (SPEC.md 29.1)."""
    icacls = _icacls_path()
    if not icacls:
        raise ChwriteError(
            "deny-user/deny-group scoped locks require icacls.exe on Windows, which was not "
            "found on PATH; a scoped ACL deny cannot be applied (SPEC.md section 29)",
            2,
        )
    applied: list[str] = []
    for name in [*deny_user, *deny_group]:
        # (WD,AD,WEA,WA), not the simple "(W)" alias - see protect_windows()
        # above for why: "(W)" silently includes DELETE, and denying
        # DELETE alone blocks ordinary file reads on Windows.
        proc = subprocess.run(
            [icacls, full_path, "/deny", f"{name}:(WD,AD,WEA,WA)"], capture_output=True, check=False
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip()
            raise ChwriteError(
                f"icacls failed to deny write access to {name!r} on {full_path}: {stderr} "
                f"(chwrite never creates users/groups - {name!r} must already resolve to a SID)",
                2,
            )
        applied.append(name)
    return {
        "backend": "windows-acl-deny",
        "level": "ENFORCED",
        "hard": False,
        "acl_entries": applied,
    }


def unprotect_windows_scoped(full_path: str, entry: FileEntry) -> None:
    """Remove exactly the deny ACEs protect_windows_scoped() added."""
    icacls = _icacls_path()
    if not icacls or not os.path.exists(full_path):
        return
    for name in entry.get("acl_entries", []):
        subprocess.run([icacls, full_path, "/remove:d", name], capture_output=True, check=False)


def query_windows_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> tuple[str, str | None]:
    """Inspect the real ACL (`icacls`) for the specific deny entries requested."""
    try:
        os.stat(full_path)
    except FileNotFoundError:
        return "MISSING", None
    names = [*deny_user, *deny_group]
    if not names:
        return "UNPROTECTED", None
    icacls = _icacls_path()
    if not icacls:
        return "UNPROTECTED", None
    proc = subprocess.run([icacls, full_path], capture_output=True, check=False)
    if proc.returncode != 0:
        return "UNPROTECTED", None
    out = proc.stdout.decode(errors="replace").upper()
    if all(f"{name.upper()}:(DENY)" in out for name in names):
        return "ENFORCED", "windows-acl-deny"
    return "UNPROTECTED", None


# --- backends/unknown.py -----------------------------------------

def protect_unknown(full_path: str, hard: bool) -> ProtectResult:
    """No-op: nothing to apply. Classified VERIFY. `hard` is unused - this
    backend never offers HARD, kept only for the uniform backend signature."""
    del hard
    return {"backend": "verify-only", "level": "VERIFY", "hard": False}


def unprotect_unknown(full_path: str, entry: FileEntry) -> None:
    """No-op: nothing was applied. `entry` unused, kept for a uniform signature."""
    del entry


def query_unknown(full_path: str) -> tuple[str, str | None]:
    """VERIFY for any existing file; MISSING if it has been deleted."""
    if not os.path.exists(full_path):
        return "MISSING", None
    return "VERIFY", "verify-only"


# --- backends/__init__.py ----------------------------------------

__all__ = [
    "BACKENDS",
    "PLATFORM",
    "SCOPED_BACKENDS",
    "detect_platform",
    "linux_acl_capability",
    "protect_path",
    "protect_path_scoped",
    "query_path",
    "unprotect_path",
]


def detect_platform() -> str:
    """Map platform.system()/os.name to one of chwrite's five backends."""
    system = platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        return "linux"
    if system == "Windows":
        return "windows"
    if os.name == "posix":
        return "posix"
    return "unknown"


PLATFORM = detect_platform()

BACKENDS = {
    "macos": (protect_macos, unprotect_macos, query_macos),
    "linux": (protect_linux, unprotect_linux, query_linux),
    "windows": (protect_windows, unprotect_windows, query_windows),
    "posix": (protect_posix, unprotect_posix, query_posix),
    "unknown": (protect_unknown, unprotect_unknown, query_unknown),
}

# Only macOS, Linux, and Windows have a real per-identity deny primitive
# (SPEC.md section 29's 29.1: NFSv4-style ACL / POSIX ACL / NTFS ACL,
# respectively). The generic POSIX chmod fallback and the VERIFY-only
# unknown-OS backend have no such mechanism, so they are deliberately
# absent here - protect_path_scoped() refuses rather than pretending.
SCOPED_BACKENDS = {
    "macos": (protect_macos_scoped, unprotect_macos_scoped, query_macos_scoped),
    "linux": (protect_linux_scoped, unprotect_linux_scoped, query_linux_scoped),
    "windows": (protect_windows_scoped, unprotect_windows_scoped, query_windows_scoped),
}


def protect_path(full_path: str, hard: bool = False) -> ProtectResult:
    """Apply the current platform's strongest available blanket protection."""
    return BACKENDS[PLATFORM][0](full_path, hard)


def protect_path_scoped(
    full_path: str, deny_user: list[str], deny_group: list[str]
) -> ProtectResult:
    """Apply a deny-user/deny-group scoped lock (SPEC.md section 29).

    Raises:
        ChwriteError: this platform has no real deny-ACE primitive, or the
            platform-specific tooling/capability required is unavailable.
            Never silently falls back to a blanket block or no-op.
    """
    if PLATFORM not in SCOPED_BACKENDS:
        raise ChwriteError(
            f"deny-user/deny-group scoped locks are not implemented on this platform "
            f"({PLATFORM}); only macOS, Linux, and Windows provide a real per-identity deny "
            "primitive (SPEC.md section 29)",
            2,
        )
    return SCOPED_BACKENDS[PLATFORM][0](full_path, deny_user, deny_group)


def unprotect_path(full_path: str, entry: FileEntry) -> None:
    """Reverse whatever protect_path()/protect_path_scoped() applied.

    Dispatches on the recorded entry's scope (section 29.2): a non-"all"
    scope was necessarily created by protect_path_scoped(), so it must be
    reversed by the matching scoped backend, not the blanket one.
    """
    scope = entry.get("scope", "all")
    if scope != "all" and PLATFORM in SCOPED_BACKENDS:
        SCOPED_BACKENDS[PLATFORM][1](full_path, entry)
        return
    BACKENDS[PLATFORM][1](full_path, entry)


def query_path(full_path: str, entry: FileEntry | None = None) -> tuple[str, str | None]:
    """Inspect the real, current OS-level protection state of full_path.

    When `entry` is given and its scope is not "all", queries the specific
    deny-user/deny-group ACL entries recorded for it instead of the
    blanket-protection state (section 29.2: status/doctor "inspects real
    ACL/ACE state ... never trusts the JSON alone" - the entry only tells
    us *which* identities to check for, not whether they are still denied).
    """
    if entry is not None:
        scope = entry.get("scope", "all")
        if scope != "all" and PLATFORM in SCOPED_BACKENDS:
            return SCOPED_BACKENDS[PLATFORM][2](
                full_path, scope_deny_user(scope), scope_deny_group(scope)
            )
    return BACKENDS[PLATFORM][2](full_path)


# --- reconcile.py ------------------------------------------------

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


# --- claude_hook.py ----------------------------------------------

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
    """
    state = load_state(root)
    policy = load_policy(root)
    entry = state["files"].get(rel)
    rule_message = None
    if policy:
        for rule in policy.rules:
            if _rule_matches(rule, rel):
                rule_message = rule.message or default_message_for(policy)
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


# --- config_paths.py ---------------------------------------------

def config_dir() -> str:
    """Per-user chwrite install directory (SPEC.md section 6)."""
    if PLATFORM == "windows":
        base = os.environ.get("APPDATA")
        if not base:
            raise ChwriteError("%APPDATA% is not set", 2)
        return os.path.join(base, "chwrite")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "chwrite")


# --- diagnostics.py ----------------------------------------------

LINUX_DENY_GROUP_CAVEAT = (
    "NOTE: deny-group is best-effort on Linux - POSIX ACL group entries are additive, so this "
    "only reliably blocks the target group if the affected user has no other group membership "
    "granting the same access (SPEC.md section 29.1). Affected files:"
)


@dataclass
class _VerifyArgs:
    quiet: bool


def _linux_deny_group_files(state: StateDoc) -> list[str]:
    """Files with an active deny-group scope on Linux (SPEC.md 29.1, 29.2)."""
    if PLATFORM != "linux":
        return []
    out: list[str] = []
    for rel, entry in sorted(state["files"].items()):
        if not entry.get("locked"):
            continue
        scope = entry.get("scope", "all")
        if isinstance(scope, dict) and scope.get("deny_group"):
            out.append(rel)
    return out


def _print_deny_group_caveat(state: StateDoc) -> None:
    """Emit the section 29.1 caveat whenever a deny-group scope is active on
    Linux - every invocation, not just once (shared by status/doctor)."""
    group_caveat_files = _linux_deny_group_files(state)
    if group_caveat_files:
        print()
        print(LINUX_DENY_GROUP_CAVEAT)
        for rel in group_caveat_files:
            print(f"  {rel}")


def cmd_status(_args: argparse.Namespace) -> int:
    """Show current protection state, inspecting real OS state (section 9)."""
    root = repo_root()
    policy = load_policy(root)
    state = load_state(root)
    files = state["files"]

    print("chwrite v1")
    print()
    print(f"Policy: {policy.path if policy else '(none)'}")
    print()

    rows: list[tuple[str, str, str]] = []
    protected_count = 0
    violation_count = 0
    for rel in sorted(files.keys()):
        entry = files[rel]
        if not entry.get("locked"):
            continue
        full = os.path.join(root, rel)
        actual_level, actual_backend = query_path(full, entry)
        if actual_level in ("MISSING", "UNPROTECTED"):
            violation_count += 1
        else:
            protected_count += 1
        rows.append((actual_level, actual_backend or entry.get("backend", "-"), rel))

    if rows:
        print(f"{'LEVEL':<10} {'BACKEND':<15} FILE")
        for level, backend, rel in rows:
            print(f"{level:<10} {backend:<15} {rel}")
        print()

    print(f"{protected_count} protected")
    print(f"{violation_count} violations")

    _print_deny_group_caveat(state)

    return 1 if violation_count else 0


def cmd_verify(args: _VerifyArgs) -> int:
    """Check protected files for modification/deletion/flag removal (17)."""
    root = repo_root()
    state = load_state(root)
    files = state["files"]
    violations: list[tuple[str, str]] = []

    for rel, entry in sorted(files.items()):
        if not entry.get("locked"):
            continue
        full = os.path.join(root, rel)
        if not os.path.exists(full):
            violations.append(("D", rel))
            continue
        proc = run_git(["status", "--porcelain", "-z", "--", rel], cwd=root, check=False)
        raw = proc.stdout.decode("utf-8", errors="surrogateescape")
        for item in filter(None, raw.split("\x00")):
            if len(item) < 3:
                continue
            code = item[:2].strip() or "?"
            path = item[3:]
            violations.append((code, path))
        actual_level, _ = query_path(full, entry)
        if actual_level == "UNPROTECTED":
            violations.append(("FLAGS-REMOVED", rel))

    if violations:
        sys.stderr.write("ERROR: chwrite violation\n\n")
        for code, path in violations:
            sys.stderr.write(f"{code} {path}\n")
        sys.stderr.write("\nRun:\n\n    chwrite status\n")
        return 1

    if not args.quiet:
        print("chwrite verify: OK")
    return 0


def _doctor_print_tools() -> None:
    tool_map = {
        "macos": ["chflags", "chmod"],
        "linux": ["chattr", "lsattr"],
        "windows": ["icacls"],
        "posix": ["chmod"],
    }
    for tool in tool_map.get(PLATFORM, []):
        found = shutil.which(tool)
        print(f"  {tool}: {'found at ' + found if found else 'NOT FOUND'}")
    if PLATFORM == "linux":
        print(f"  ACL support (deny-user/deny-group): {linux_acl_capability()}")


def _doctor_print_hooks_path(cfg_dir: str) -> None:
    proc = subprocess.run(
        ["git", "config", "--global", "--get", "core.hooksPath"], capture_output=True, check=False
    )
    hooks_path = proc.stdout.decode(errors="replace").strip() if proc.returncode == 0 else None
    expected = os.path.join(cfg_dir, "hooks")
    if hooks_path:
        matches = os.path.realpath(hooks_path) == os.path.realpath(expected)
        suffix = " (matches chwrite install)" if matches else " (points elsewhere)"
        print(f"core.hooksPath: {hooks_path}{suffix}")
    else:
        print("core.hooksPath: not set")


def _doctor_print_privilege_note() -> None:
    if PLATFORM == "linux":
        is_root = hasattr(os, "geteuid") and os.geteuid() == 0
        privilege_note = (
            "root (HARD protection available)"
            if is_root
            else "unprivileged (chattr +i will require sudo)"
        )
        print(f"privilege: {privilege_note}")
    elif PLATFORM == "macos":
        print("privilege: uchg (ENFORCED) requires no elevation for files you own;")
        print("           schg is not implemented")
    elif PLATFORM == "windows":
        print("privilege: icacls deny ACE (ENFORCED) requires no elevation for files you own")


def _doctor_print_repo_section() -> None:
    try:
        root = repo_root()
        print()
        print(f"Repository: {root}")
        filename = find_policy_file(root)
        print(f"Policy file: {filename if filename else '(none)'}")
        _print_deny_group_caveat(load_state(root))
    except ChwriteError as e:
        print()
        print(f"Not currently inside a git repository ({e})")


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Diagnose OS/backend/install/hook health (SPEC.md section 21, 29.1)."""
    print("chwrite doctor")
    print()
    print(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Detected backend: {PLATFORM}")
    _doctor_print_tools()

    git_path = shutil.which("git")
    if git_path:
        proc = subprocess.run(["git", "--version"], capture_output=True, check=False)
        print(f"git: {proc.stdout.decode(errors='replace').strip()} ({git_path})")
    else:
        print("git: NOT FOUND (required)")

    cfg_dir = config_dir()
    dest_py = os.path.join(cfg_dir, "chwrite.py")
    install_note = dest_py if os.path.isfile(dest_py) else "not installed (run chwrite install)"
    print(f"global install: {install_note}")

    _doctor_print_hooks_path(cfg_dir)
    _doctor_print_privilege_note()
    _doctor_print_repo_section()
    return 0


# --- cli.py ------------------------------------------------------

# The dataclasses below give each cmd_*() function's body real static
# typing for its arguments. At runtime argparse hands every command a
# plain argparse.Namespace (see main()); its attributes match these
# fields structurally (argparse sets them by the `dest` name), so this is
# the standard "lie to the type checker at the untyped boundary" idiom
# for making the rest of an argparse-based CLI checkable under strict
# mode without a third-party typed-argparse dependency.


@dataclass
class _InitArgs:
    format: str


@dataclass
class _AddArgs:
    pathspec: str
    message: str | None


@dataclass
class _RemoveArgs:
    pathspec: str


@dataclass
class _ApplyArgs:
    quiet: bool


@dataclass
class _LockArgs:
    path: str | None
    hard: bool
    message: str | None
    deny_user: str | None
    deny_group: str | None


@dataclass
class _UnlockArgs:
    path: str | None
    all: bool


@dataclass
class _UnlockedArgs:
    command: list[str]


def _split_names(raw: str | None) -> list[str]:
    """Parse a comma-separated --deny-user/--deny-group CLI value."""
    if not raw:
        return []
    return [n.strip() for n in raw.split(",") if n.strip()]


def cmd_init(args: _InitArgs) -> int:
    """Create a new, empty policy file (SPEC.md section 20)."""
    root = repo_root()
    existing = find_policy_file(root)
    if existing:
        raise ChwriteError(f"a chwrite policy file already exists: {existing}", 2)
    filename = {
        "plain": ".chwrite",
        "json": ".chwrite.json",
        "toml": ".chwrite.toml",
        "yaml": ".chwrite.yaml",
    }[args.format]
    path = os.path.join(root, filename)
    POLICY_WRITERS[filename](path, 1, [])
    print(f"created {filename}")
    return 0


def cmd_add(args: _AddArgs) -> int:
    """Add (or update) a protect rule in the existing policy file."""
    root = repo_root()
    filename = find_policy_file(root)
    if filename is None:
        raise ChwriteError("no chwrite policy file found; run 'chwrite init' first", 2)
    policy = load_policy(root)
    assert policy is not None  # find_policy_file already confirmed one exists
    validate_pathspec(args.pathspec, root)
    rules = list(policy.rules)
    for idx, r in enumerate(rules):
        if r.pattern == args.pathspec:
            rules[idx] = Rule(
                args.pathspec,
                args.message if args.message is not None else r.message,
                r.regex,
                r.deny_user,
                r.deny_group,
            )
            break
    else:
        rules.append(Rule(args.pathspec, args.message))
    POLICY_WRITERS[filename](policy.path, policy.version, rules)
    suffix = f' message="{args.message}"' if args.message else ""
    print(f"protect {args.pathspec}{suffix}  ({filename})")
    return 0


def cmd_remove(args: _RemoveArgs) -> int:
    """Remove a protect rule from the existing policy file."""
    root = repo_root()
    filename = find_policy_file(root)
    if filename is None:
        raise ChwriteError("no chwrite policy file found; run 'chwrite init' first", 2)
    policy = load_policy(root)
    assert policy is not None
    rules = [r for r in policy.rules if r.pattern != args.pathspec]
    if len(rules) == len(policy.rules):
        sys.stderr.write(f"no rule found for pathspec: {args.pathspec}\n")
        return 1
    POLICY_WRITERS[filename](policy.path, policy.version, rules)
    print(f"removed {args.pathspec}  ({filename})")
    return 0


def cmd_apply(args: _ApplyArgs) -> int:
    """(Re)apply protection according to the policy file. Idempotent."""
    root = repo_root()
    state = load_state(root)
    _, report = reconcile(root, state, hard_all=False)
    save_state(root, state)
    if not args.quiet:
        if not report:
            print("chwrite apply: nothing to do (already up to date)")
        else:
            for event in report:
                if event.kind in ("locked", "relocked"):
                    print(f"{event.kind} {event.rel}  [{event.level}]")
                elif event.kind == "removed":
                    print(f"unprotected {event.rel}  (no longer in policy)")
    return 0


def _cmd_lock_single(args: _LockArgs, root: str, state: StateDoc) -> int:
    assert args.path is not None
    rel = normalize_local_arg_path(args.path, root)
    full = os.path.join(root, rel)
    if not os.path.exists(full):
        raise ChwriteError(f"no such file: {args.path}", 2)
    if not check_symlink_safety(full, root):
        raise ChwriteError(f"refusing to protect symlink pointing outside repo root: {rel}", 2)
    entry = state["files"].get(rel)
    original_mode = determine_original_mode(full, entry)
    message = args.message
    if message is None and entry and entry.get("source") == "adhoc":
        message = entry.get("message")
    if message is None:
        message = ADHOC_DEFAULT_MESSAGE

    deny_user = _split_names(args.deny_user)
    deny_group = _split_names(args.deny_group)
    scope = make_scope(deny_user, deny_group)
    if scope != "all":
        if args.hard:
            raise ChwriteError(
                "--hard cannot be combined with --deny-user/--deny-group scoped locks "
                "(scoped ACL-deny backends only offer ENFORCED, never HARD - SPEC.md section 29)",
                2,
            )
        result = protect_path_scoped(full, deny_user, deny_group)
    else:
        result = protect_path(full, hard=args.hard)

    new_entry: FileEntry = {
        "backend": result["backend"],
        "level": result["level"],
        "original_mode": original_mode,
        "locked": True,
        "source": "adhoc",
        "message": message,
        "hard": result.get("hard", False),
        "scope": scope,
    }
    if "acl_user" in result:
        new_entry["acl_user"] = result["acl_user"]
    if "acl_entries" in result:
        new_entry["acl_entries"] = result["acl_entries"]
    state["files"][rel] = new_entry
    save_state(root, state)
    print(f"locked {rel}  [{result['level']}]")
    if args.hard and result["level"] != "HARD":
        return 1
    return 0


def cmd_lock(args: _LockArgs) -> int:
    """Lock all protected files, or ad hoc lock a single path (24.4, 29)."""
    root = repo_root()
    state = load_state(root)

    if args.path:
        return _cmd_lock_single(args, root, state)

    if args.deny_user or args.deny_group:
        raise ChwriteError(
            "--deny-user/--deny-group require a <path> (ad hoc lock); scope for policy-driven "
            "files comes from the policy file's own deny-user=/deny-group= rules instead "
            "(SPEC.md section 29)",
            2,
        )

    _, report = reconcile(root, state, hard_all=args.hard)
    save_state(root, state)
    exit_code = 0
    if not report:
        print("nothing to do (already up to date)")
    for event in report:
        if event.kind in ("locked", "relocked"):
            print(f"{event.kind} {event.rel}  [{event.level}]")
            if args.hard and event.level != "HARD":
                exit_code = 1
        elif event.kind == "removed":
            print(f"unprotected {event.rel}  (no longer in policy)")
    return exit_code


def cmd_unlock(args: _UnlockArgs) -> int:
    """Unlock a single protected file, or every protected file (--all)."""
    root = repo_root()
    state = load_state(root)
    files = state["files"]

    if args.all:
        count = 0
        for rel, entry in files.items():
            if entry.get("locked"):
                full = os.path.join(root, rel)
                if os.path.exists(full):
                    unprotect_path(full, entry)
                entry["locked"] = False
                count += 1
        save_state(root, state)
        print(f"unlocked {count} file(s)")
        return 0

    if not args.path:
        raise ChwriteError("unlock requires a <path> or --all", 2)
    rel = normalize_local_arg_path(args.path, root)
    entry = files.get(rel)
    if not entry or not entry.get("locked"):
        print(f"{rel} is not currently locked")
        return 0
    full = os.path.join(root, rel)
    if os.path.exists(full):
        unprotect_path(full, entry)
    entry["locked"] = False
    save_state(root, state)
    print(f"unlocked {rel}")
    return 0


def _reprotect_entry(full: str, entry: FileEntry) -> None:
    """Reapply whatever protect_path()/protect_path_scoped() previously
    applied to `entry`, using its own recorded scope (SPEC.md 16, 29).
    Mutates `entry` in place with the (possibly refreshed) backend/level.
    """
    scope = entry.get("scope", "all")
    if scope != "all":
        result = protect_path_scoped(full, scope.get("deny_user", []), scope.get("deny_group", []))
    else:
        result = protect_path(full, hard=entry.get("hard", False))
    entry["backend"] = result["backend"]
    entry["level"] = result["level"]
    if "acl_user" in result:
        entry["acl_user"] = result["acl_user"]
    if "acl_entries" in result:
        entry["acl_entries"] = result["acl_entries"]


def cmd_unlocked(args: _UnlockedArgs) -> int:
    """Unlock, run a command, then reapply protection (SPEC.md section 16)."""
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ChwriteError("usage: chwrite unlocked -- <command> [args...]", 2)

    root = repo_root()
    state = load_state(root)
    files = state["files"]
    locked_paths = [rel for rel, e in files.items() if e.get("locked")]
    for rel in locked_paths:
        entry = files[rel]
        full = os.path.join(root, rel)
        if os.path.exists(full):
            unprotect_path(full, entry)
        entry["locked"] = False
    save_state(root, state)

    try:
        proc = subprocess.run(command, check=False)
        code = proc.returncode
    finally:
        state = load_state(root)
        files = state["files"]
        for rel in locked_paths:
            entry = files.get(rel)
            if entry is None:
                continue
            full = os.path.join(root, rel)
            if os.path.exists(full):
                if check_symlink_safety(full, root):
                    _reprotect_entry(full, entry)
                else:
                    sys.stderr.write(
                        f"warning: refusing to reprotect symlink outside repo root: {rel}\n"
                    )
            entry["locked"] = True
        save_state(root, state)

    return code


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse parser and all subcommands."""
    p = argparse.ArgumentParser(
        prog="chwrite", description="Protect files in a Git repo from unwanted modification."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="create a chwrite policy file")
    sp.add_argument("--format", choices=["plain", "json", "toml", "yaml"], default="plain")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add", help="add a protect rule to the policy file")
    sp.add_argument("pathspec")
    sp.add_argument("--message", default=None)
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="remove a protect rule from the policy file")
    sp.add_argument("pathspec")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser(
        "apply", help="(re)apply protection according to the policy file (idempotent)"
    )
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("lock", help="lock all protected files, or ad hoc lock a single path")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--hard", action="store_true")
    sp.add_argument("--message", default=None)
    sp.add_argument(
        "--deny-user",
        default=None,
        help="ad hoc scoped lock: deny only this user (comma-separated)",
    )
    sp.add_argument(
        "--deny-group",
        default=None,
        help="ad hoc scoped lock: deny only this group (comma-separated)",
    )
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("unlock", help="unlock a protected file")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_unlock)

    sp = sub.add_parser(
        "unlocked", help="unlock, run a command, then reapply protection regardless of exit status"
    )
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_unlocked)

    sp = sub.add_parser("status", help="show current protection state (inspects real OS state)")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser(
        "verify", help="check protected files for modification/deletion/flag removal"
    )
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("check-path", help="fast check whether a path is protected")
    sp.add_argument("path", nargs="?", default=None)
    sp.add_argument(
        "--claude-hook",
        action="store_true",
        help="read a Claude Code PreToolUse payload from stdin",
    )
    sp.set_defaults(func=cmd_check_path)

    sp = sub.add_parser("doctor", help="diagnose chwrite installation/backend health")
    sp.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: parse argv, dispatch, and map errors to exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    try:
        return int(func(args))
    except ChwriteError as e:
        sys.stderr.write(f"chwrite: {e}\n")
        return e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
