"""Policy file parsing, writing, and resolution (SPEC.md 5, 24, 28, 29, 33)."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass

from chwrite.errors import ChwriteError
from chwrite.gitutil import (
    branch_matches,
    current_branch,
    run_git,
    validate_pathspec,
    validate_resolved_path,
)
from chwrite.policy_yaml import parse_yaml_policy

# Single source of truth for the repo policy file's basename (SPEC.md
# section 5) - every filename variant below derives from this, so
# renaming it is a one-line change, not a repo-wide find/replace.
POLICY_BASENAME = "write_protect"
POLICY_FILENAME_PLAIN = f".{POLICY_BASENAME}"
POLICY_FILENAME_JSON = f".{POLICY_BASENAME}.json"
POLICY_FILENAME_TOML = f".{POLICY_BASENAME}.toml"
POLICY_FILENAME_YAML = f".{POLICY_BASENAME}.yaml"
POLICY_FILENAME_YML = f".{POLICY_BASENAME}.yml"
POLICY_FILENAMES = [
    POLICY_FILENAME_PLAIN,
    POLICY_FILENAME_JSON,
    POLICY_FILENAME_TOML,
    POLICY_FILENAME_YAML,
    POLICY_FILENAME_YML,
]
ADHOC_DEFAULT_MESSAGE = "protected by chwrite (ad hoc local lock)"


@dataclass(frozen=True, slots=True)
class Rule:
    """A single `protect`/`protect-regex` rule.

    Exactly one of `pattern` (a Git pathspec, section 5) or `regex` (a
    Python `re` pattern searched against tracked paths, section 28) is
    set - never both, never neither. `deny_user`/`deny_group` narrow the
    rule to an optional, non-blanket scope (section 29); both empty means
    the default blanket-block behavior of sections 10-14. `branches`
    narrows the rule to an optional set of branch-name glob patterns
    (section 33); empty means the rule is unconditional (matches on every
    branch, including detached HEAD) - the pre-section-33 default.
    """

    pattern: str | None
    message: str | None = None
    regex: str | None = None
    deny_user: tuple[str, ...] = ()
    deny_group: tuple[str, ...] = ()
    branches: tuple[str, ...] = ()


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


def branch_condition_note(rule: Rule, branch: str | None) -> str:
    """Suffix naming WHICH branch condition locked a file (SPEC.md 33.4).

    Empty string for an unconditional rule (`rule.branches` empty) - this
    is purely additive and never changes a rule with no branch condition.
    For a branch-scoped rule, this is appended to whatever message would
    otherwise be shown (explicit `message=...` or the 24.3 default) so a
    blocked write always says *why*, not just *that*, it's blocked.
    """
    if not rule.branches:
        return ""
    joined = ",".join(rule.branches)
    if branch is None:
        return f' [branches="{joined}": HEAD is detached, branch-scoped rules apply by default]'
    return f' [branches="{joined}": active on current branch "{branch}"]'


VERSION_LINE_RE = re.compile(r"^version\s+(\d+)$")
PROTECT_LINE_RE = re.compile(r"^protect\s+(.*)$")
PROTECT_REGEX_LINE_RE = re.compile(r"^protect-regex\s+(.*)$")

# A single trailing `key="quoted value"` or `key=bareword` option, anchored
# to the end of the line. Repeatedly stripping matches of this from the end
# lets message=/deny-user=/deny-group=/branches= appear in any order/
# combination after the pathspec or regex body, same as the original
# message-only grammar (section 24.1) extended for section 29's scope
# options and section 33's branch condition.
_TRAILING_OPTION_RE = re.compile(
    r"^(?P<rest>.*)\s+(?P<key>message|deny-user|deny-group|branches)="
    r'(?P<value>"(?:[^"\\]|\\.)*"|\S+)$'
)
_KNOWN_OPTIONS = {"message", "deny-user", "deny-group", "branches"}


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


def _parse_csv_value(raw: str | None, key: str, filename: str, lineno: int) -> tuple[str, ...]:
    """Split a comma-separated `key=` value into names.

    Shared by deny-user=/deny-group= (section 29, plain names) and
    branches= (section 33, fnmatch glob patterns) - all three are "a
    comma-separated list of strings" at the grammar level; what each list
    element means is entirely up to the caller.
    """
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
    deny_user = _parse_csv_value(options.get("deny-user"), "deny-user", filename, lineno)
    deny_group = _parse_csv_value(options.get("deny-group"), "deny-group", filename, lineno)
    branches = _parse_csv_value(options.get("branches"), "branches", filename, lineno)
    return Rule(pattern, message, regex, deny_user, deny_group, branches)


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


def _string_list_from_structured(
    value: object, key: str, filename: str, idx: int
) -> tuple[str, ...]:
    """Validate protect[idx].<key> as a JSON/TOML list of non-empty strings.

    Shared by deny_user/deny_group (section 29) and branches (section 33) -
    unlike the plain/YAML formats' comma-separated scalar, JSON and TOML
    both have native list syntax, so the structured formats use it.
    """
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
        deny_user = _string_list_from_structured(
            item_map.get("deny_user"), "deny_user", filename, i
        )
        deny_group = _string_list_from_structured(
            item_map.get("deny_group"), "deny_group", filename, i
        )
        branches = _string_list_from_structured(item_map.get("branches"), "branches", filename, i)
        rules.append(Rule(pattern, message, regex, deny_user, deny_group, branches))  # pyright: ignore[reportUnknownArgumentType]
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
            "interpreter); use .write_protect, .write_protect.json, or .write_protect.yaml instead",
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
    unknown = set(item.keys()) - {
        "pattern",
        "regex",
        "message",
        "deny_user",
        "deny_group",
        "branches",
    }
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
    # module docstring), so deny_user/deny_group/branches are all written
    # the same way as the plain format: a single comma-separated scalar,
    # not a list.
    deny_user = _parse_csv_value(item.get("deny_user"), "deny_user", filename, idx)
    deny_group = _parse_csv_value(item.get("deny_group"), "deny_group", filename, idx)
    branches = _parse_csv_value(item.get("branches"), "branches", filename, idx)
    return Rule(pattern, message, regex, deny_user, deny_group, branches)


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
    if filename == POLICY_FILENAME_PLAIN:
        version, rules = _parse_plain(text, filename)
    elif filename == POLICY_FILENAME_JSON:
        version, rules = _parse_json(text, filename)
    elif filename == POLICY_FILENAME_TOML:
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
    """Resolve every rule (pathspec or regex) via `git ls-files` (SPEC.md 5, 28, 33).

    Pathspec rules resolve via `git ls-files -z -- <pathspec>`. Regex rules
    never resolve paths themselves - they `re.search()` against the same
    `git ls-files -z` (whole-repo) candidate list pathspec rules use, so
    the path-traversal/symlink-escape protections in section 18 apply
    identically either way (section 28).

    A rule with a `branches=` condition (section 33) that does not match
    the current branch (see `chwrite.gitutil.branch_matches`) contributes
    nothing to the returned mapping at all - as far as apply/lock/status
    are concerned, an inactive-on-this-branch rule looks exactly like a
    rule that was never in the policy to begin with. This is deliberate:
    it means reconcile()'s existing "no longer in policy -> unprotect"
    handling (SPEC.md 7-8) is *also* what unprotects a file when a branch
    condition goes from active to inactive across a checkout, with no
    separate code path to keep in sync.

    Returns:
        {repo_relative_path: ResolvedProtection}. A file matched by more
        than one *active* rule is protected exactly once; the *last*-
        defined matching rule wins for message/scope purposes (policy
        file order, section 28's documented precedence) - branch-inactive
        rules are simply never candidates for that precedence at all.
    """
    mapping: dict[str, ResolvedProtection] = {}
    if policy is None:
        return mapping
    all_files: list[str] | None = None
    branch: str | None = None
    branch_resolved = False
    for rule in policy.rules:
        if rule.branches:
            if not branch_resolved:
                branch = current_branch(root)
                branch_resolved = True
            if not branch_matches(branch, rule.branches):
                continue
        message = rule.message if rule.message else default_message_for(policy)
        message += branch_condition_note(rule, branch)
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
    """Serialize rules to the plain `.write_protect` format."""
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
        if r.branches:
            line += f" branches={','.join(r.branches)}"
        lines.append(line)
    _write_text(path, "\n".join(lines) + "\n")


def write_json(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to `.write_protect.json`."""
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
        if r.branches:
            item["branches"] = list(r.branches)
        protect.append(item)
    _write_text(path, json.dumps({"version": version, "protect": protect}, indent=2) + "\n")


def write_toml(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to `.write_protect.toml`."""
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
        if r.branches:
            lines.append("branches = [" + ", ".join(_toml_quote(n) for n in r.branches) + "]")
    _write_text(path, "\n".join(lines) + "\n")


def write_yaml(path: str, version: int, rules: list[Rule]) -> None:
    """Serialize rules to `.write_protect.yaml`."""
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
            if r.branches:
                lines.append(f"    branches: {_toml_quote(','.join(r.branches))}")
    _write_text(path, "\n".join(lines) + "\n")


POLICY_WRITERS = {
    POLICY_FILENAME_PLAIN: write_plain,
    POLICY_FILENAME_JSON: write_json,
    POLICY_FILENAME_TOML: write_toml,
    POLICY_FILENAME_YAML: write_yaml,
    POLICY_FILENAME_YML: write_yaml,
}
