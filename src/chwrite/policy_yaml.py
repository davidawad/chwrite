"""YAML-subset parser for .write_protect.yaml/.yml policy files (SPEC.md 24.2, 28, 29, 33).

This is NOT a general YAML parser. It supports exactly: a top-level
mapping with a scalar "version" key and a "protect" block-sequence of
mappings with string "pattern"/"regex"/"message"/"deny_user"/"deny_group"/
"branches" keys, "#" comments, and 2-space indentation. Anchors,
multi-document streams, flow collections, and non-string scalars are
explicitly rejected rather than silently misinterpreted, per SPEC.md
section 24.2. Because flow sequences aren't supported, deny_user/
deny_group/branches are written as a single comma-separated scalar (like
the plain format's `deny-user=a,b`), not a YAML list - policy.py is
responsible for splitting that string and for all cross-field validation
(exactly one of pattern/regex, etc.); this module only extracts the raw
key/value structure.
"""

from __future__ import annotations

import re

from chwrite.errors import ChwriteError

YAML_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$")

_PROTECT_ITEM_KEYS = {"pattern", "regex", "message", "deny_user", "deny_group", "branches"}


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
    """Parse a .write_protect.yaml/.yml document into (version, raw protect items).

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
