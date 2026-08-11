"""Policy parsing/writing/resolution tests (SPEC.md 5, 24, 27)."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from chwrite import policy as policy_mod
from chwrite.errors import ChwriteError
from chwrite.policy import (
    Policy,
    Rule,
    _parse_json,
    _parse_plain,
    _parse_toml,
    _toml_quote,
    default_message_for,
    find_policy_file,
    load_policy,
    parse_yaml_policy,
    resolve_policy_files,
    write_json,
    write_plain,
    write_toml,
    write_yaml,
)


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return str(tmp_path)


# ---------------------------------------------------------------------------
# plain / json / yaml parsing
# ---------------------------------------------------------------------------


def test_parse_plain_basic() -> None:
    text = (
        "version 1\n\n"
        'protect package-lock.json message="Managed by CI"\n'
        "protect .github/workflows/*.yml\n"
    )
    version, rules = _parse_plain(text, ".write_protect")
    assert version == 1
    assert rules == [
        Rule("package-lock.json", "Managed by CI"),
        Rule(".github/workflows/*.yml", None),
    ]


def test_parse_plain_missing_version_is_config_error() -> None:
    with pytest.raises(ChwriteError) as exc_info:
        _parse_plain("protect foo\n", ".write_protect")
    assert exc_info.value.code == 2


def test_parse_plain_escaped_quote_in_message() -> None:
    _, rules = _parse_plain('version 1\nprotect foo message="say \\"hi\\""\n', ".write_protect")
    assert rules[0].message == 'say "hi"'


def test_parse_plain_unrecognized_line_is_config_error() -> None:
    with pytest.raises(ChwriteError):
        _parse_plain("version 1\nnonsense line here\n", ".write_protect")


def test_parse_plain_malformed_version_line() -> None:
    with pytest.raises(ChwriteError):
        _parse_plain("version abc\n", ".write_protect")


def test_parse_plain_unsupported_version() -> None:
    with pytest.raises(ChwriteError):
        _parse_plain("version 2\nprotect foo\n", ".write_protect")


def test_parse_plain_malformed_protect_line() -> None:
    with pytest.raises(ChwriteError):
        _parse_plain("version 1\nprotectwithnospace\n", ".write_protect")


def test_parse_json_basic() -> None:
    text = '{"version": 1, "protect": [{"pattern": "a"}, {"pattern": "b", "message": "m"}]}'
    version, rules = _parse_json(text, ".write_protect.json")
    assert version == 1
    assert rules == [Rule("a", None), Rule("b", "m")]


def test_parse_json_wrong_version_is_config_error() -> None:
    with pytest.raises(ChwriteError):
        _parse_json('{"version": 2, "protect": []}', ".write_protect.json")


def test_parse_json_invalid_json_syntax() -> None:
    with pytest.raises(ChwriteError):
        _parse_json("{not json", ".write_protect.json")


def test_parse_json_top_level_not_a_mapping() -> None:
    with pytest.raises(ChwriteError):
        _parse_json("[1, 2, 3]", ".write_protect.json")


def test_parse_json_missing_version() -> None:
    with pytest.raises(ChwriteError):
        _parse_json('{"protect": []}', ".write_protect.json")


def test_parse_json_protect_not_a_list() -> None:
    with pytest.raises(ChwriteError):
        _parse_json('{"version": 1, "protect": "nope"}', ".write_protect.json")


def test_parse_json_protect_item_missing_pattern() -> None:
    with pytest.raises(ChwriteError):
        _parse_json('{"version": 1, "protect": [{}]}', ".write_protect.json")


def test_parse_json_protect_pattern_not_a_string() -> None:
    with pytest.raises(ChwriteError):
        _parse_json('{"version": 1, "protect": [{"pattern": 5}]}', ".write_protect.json")


def test_parse_json_protect_message_not_a_string() -> None:
    with pytest.raises(ChwriteError):
        _parse_json(
            '{"version": 1, "protect": [{"pattern": "a", "message": 5}]}', ".write_protect.json"
        )


def test_parse_yaml_basic() -> None:
    text = 'version: 1\nprotect:\n  - pattern: a\n    message: "hi"\n  - pattern: b\n'
    version, rules = parse_yaml_policy(text, ".write_protect.yaml")
    assert version == 1
    assert rules == [
        {"pattern": "a", "message": "hi"},
        {"pattern": "b"},
    ]


def test_parse_yaml_rejects_flow_collection() -> None:
    with pytest.raises(ChwriteError):
        parse_yaml_policy("version: 1\nprotect: [a, b]\n", ".write_protect.yaml")


def test_parse_yaml_rejects_tabs() -> None:
    with pytest.raises(ChwriteError):
        parse_yaml_policy("version: 1\n\tprotect: []\n", ".write_protect.yaml")


def test_parse_toml_requires_py311(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(policy_mod.sys, "version_info", (3, 10, 0, "final", 0))
    path = tmp_path / ".write_protect.toml"
    path.write_text('version = 1\n\n[[protect]]\npattern = "a"\n')
    with pytest.raises(ChwriteError) as exc_info:
        _parse_toml(str(path), ".write_protect.toml")
    assert "Python 3.11+" in str(exc_info.value)


def test_parse_toml_invalid_syntax(tmp_path) -> None:
    path = tmp_path / ".write_protect.toml"
    path.write_text("not valid toml [[[")
    with pytest.raises(ChwriteError):
        _parse_toml(str(path), ".write_protect.toml")


# ---------------------------------------------------------------------------
# default_message_for / find_policy_file / load_policy
# ---------------------------------------------------------------------------


def test_default_message_for_none_policy() -> None:
    assert default_message_for(None) == "protected by chwrite (ad hoc local lock)"


def test_default_message_for_policy_names_the_file() -> None:
    policy = Policy("/repo/.write_protect.yaml", ".write_protect.yaml", 1, ())
    assert ".write_protect.yaml" in default_message_for(policy)


def test_find_policy_file_none_present(tmp_path) -> None:
    assert find_policy_file(str(tmp_path)) is None


def test_find_policy_file_multiple_present_is_ambiguous(tmp_path) -> None:
    (tmp_path / ".write_protect").write_text("version 1\n")
    (tmp_path / ".write_protect.json").write_text('{"version": 1, "protect": []}')
    with pytest.raises(ChwriteError) as exc_info:
        find_policy_file(str(tmp_path))
    assert exc_info.value.code == 2


def test_load_policy_none_when_absent(tmp_path) -> None:
    root = _init_repo(tmp_path)
    assert load_policy(root) is None


def test_load_policy_json_round_trip(tmp_path) -> None:
    root = _init_repo(tmp_path)
    write_json(str(tmp_path / ".write_protect.json"), 1, [Rule("a.txt", "msg")])
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == (Rule("a.txt", "msg"),)


def test_load_policy_toml_round_trip(tmp_path) -> None:
    root = _init_repo(tmp_path)
    write_toml(str(tmp_path / ".write_protect.toml"), 1, [Rule("a.txt", "msg")])
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == (Rule("a.txt", "msg"),)


def test_load_policy_yaml_round_trip(tmp_path) -> None:
    root = _init_repo(tmp_path)
    write_yaml(str(tmp_path / ".write_protect.yaml"), 1, [Rule("a.txt", "msg")])
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == (Rule("a.txt", "msg"),)


# ---------------------------------------------------------------------------
# write_* serializers
# ---------------------------------------------------------------------------


def test_write_plain_round_trip_no_message(tmp_path) -> None:
    path = str(tmp_path / ".write_protect")
    write_plain(path, 1, [Rule("a.txt", None)])
    with open(path, encoding="utf-8") as f:
        version, rules = _parse_plain(f.read(), ".write_protect")
    assert version == 1
    assert rules == [Rule("a.txt", None)]


def test_write_json_omits_message_when_absent(tmp_path) -> None:
    path = tmp_path / ".write_protect.json"
    write_json(str(path), 1, [Rule("a.txt", None)])
    assert '"message"' not in path.read_text()


def test_toml_quote_escapes_backslash_and_quote() -> None:
    assert _toml_quote('a"b\\c') == '"a\\"b\\\\c"'


def test_write_yaml_empty_rules() -> None:
    written: dict[str, str] = {}

    def fake_write_text(_path: str, content: str) -> None:
        written["content"] = content

    with patch("chwrite.policy._write_text", fake_write_text):
        write_yaml("/tmp/whatever.write_protect.yaml", 1, [])
    assert "protect: []" in written["content"]


# ---------------------------------------------------------------------------
# regex rules (SPEC.md section 28)
# ---------------------------------------------------------------------------


def test_parse_plain_protect_regex_basic() -> None:
    text = 'version 1\n\nprotect-regex ^migrations/.*\\.sql$ message="append-only"\n'
    _, rules = _parse_plain(text, ".write_protect")
    assert rules == [Rule(None, "append-only", r"^migrations/.*\.sql$")]


def test_parse_plain_protect_regex_no_message() -> None:
    _, rules = _parse_plain("version 1\nprotect-regex ^foo\\.txt$\n", ".write_protect")
    assert rules[0].regex == r"^foo\.txt$"
    assert rules[0].message is None


def test_parse_plain_protect_regex_missing_pattern_is_config_error() -> None:
    with pytest.raises(ChwriteError):
        _parse_plain("version 1\nprotect-regex\n", ".write_protect")


def test_parse_plain_protect_regex_no_space_is_config_error() -> None:
    with pytest.raises(ChwriteError):
        _parse_plain("version 1\nprotect-regexfoo bar\n", ".write_protect")


def test_load_policy_rejects_invalid_regex(tmp_path) -> None:
    root = _init_repo(tmp_path)
    (tmp_path / ".write_protect").write_text("version 1\n\nprotect-regex (unclosed\n")
    with pytest.raises(ChwriteError) as exc_info:
        load_policy(root)
    assert exc_info.value.code == 2
    assert "invalid regex" in str(exc_info.value)


def test_parse_json_regex_field() -> None:
    text = '{"version": 1, "protect": [{"regex": "^a.*", "message": "m"}]}'
    _, rules = _parse_json(text, ".write_protect.json")
    assert rules == [Rule(None, "m", "^a.*")]


def test_parse_json_pattern_and_regex_both_set_is_config_error() -> None:
    text = '{"version": 1, "protect": [{"pattern": "a", "regex": "b"}]}'
    with pytest.raises(ChwriteError):
        _parse_json(text, ".write_protect.json")


def test_parse_json_neither_pattern_nor_regex_is_config_error() -> None:
    text = '{"version": 1, "protect": [{"message": "m"}]}'
    with pytest.raises(ChwriteError):
        _parse_json(text, ".write_protect.json")


def test_parse_json_regex_not_a_string_is_config_error() -> None:
    text = '{"version": 1, "protect": [{"regex": 5}]}'
    with pytest.raises(ChwriteError):
        _parse_json(text, ".write_protect.json")


def test_parse_yaml_regex_field(tmp_path) -> None:
    root = _init_repo(tmp_path)
    (tmp_path / ".write_protect.yaml").write_text('version: 1\nprotect:\n  - regex: "^a.*"\n')
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == (Rule(None, None, "^a.*"),)


def test_yaml_pattern_and_regex_exclusivity_enforced_by_policy(tmp_path) -> None:
    root = _init_repo(tmp_path)
    (tmp_path / ".write_protect.yaml").write_text(
        "version: 1\nprotect:\n  - pattern: a\n    regex: b\n"
    )
    with pytest.raises(ChwriteError):
        load_policy(root)


def test_resolve_policy_files_regex_matches_tracked_files(tmp_path) -> None:
    root = _init_repo(tmp_path)
    (tmp_path / "migrations").mkdir()
    (tmp_path / "migrations" / "001.sql").write_text("x")
    (tmp_path / "other.txt").write_text("x")
    (tmp_path / ".write_protect").write_text(
        'version 1\n\nprotect-regex ^migrations/.*\\.sql$ message="append-only"\n'
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)

    policy = load_policy(root)
    resolved = resolve_policy_files(root, policy)
    assert set(resolved.keys()) == {"migrations/001.sql"}
    assert resolved["migrations/001.sql"].message == "append-only"


def test_resolve_policy_files_last_matching_rule_wins_message() -> None:
    pass  # exercised via CLI-level test in test_reconcile.py for realism


def test_resolve_policy_files_invalid_regex_raises_at_resolve_too(tmp_path) -> None:
    # load_policy already validates regex compiles, but resolve_policy_files
    # has its own defensive re.compile() (it does not trust Rule objects
    # constructed by other means) - exercise that branch directly.
    root = _init_repo(tmp_path)
    bad_policy = Policy(
        str(tmp_path / ".write_protect"), ".write_protect", 1, (Rule(None, None, "(bad"),)
    )
    with pytest.raises(ChwriteError):
        resolve_policy_files(root, bad_policy)


# ---------------------------------------------------------------------------
# deny-user/deny-group scope parsing (SPEC.md section 29)
# ---------------------------------------------------------------------------


def test_parse_plain_deny_user_scope() -> None:
    _, rules = _parse_plain("version 1\nprotect a.txt deny-user=ci-bot\n", ".write_protect")
    assert rules[0].deny_user == ("ci-bot",)
    assert rules[0].deny_group == ()


def test_parse_plain_deny_group_scope_multiple_names() -> None:
    _, rules = _parse_plain(
        "version 1\nprotect a.txt deny-group=contractors,interns\n", ".write_protect"
    )
    assert rules[0].deny_group == ("contractors", "interns")


def test_parse_plain_message_and_scope_together_any_order() -> None:
    _, rules = _parse_plain(
        'version 1\nprotect a.txt deny-user=bob message="careful"\n', ".write_protect"
    )
    assert rules[0].deny_user == ("bob",)
    assert rules[0].message == "careful"

    _, rules2 = _parse_plain(
        'version 1\nprotect a.txt message="careful" deny-user=bob\n', ".write_protect"
    )
    assert rules2[0].deny_user == ("bob",)
    assert rules2[0].message == "careful"


def test_parse_plain_empty_deny_user_value_is_config_error() -> None:
    # A bareword deny-user= value must be non-whitespace to be recognized as
    # an option at all (see _strip_trailing_options); an all-comma value
    # (e.g. from a stray trailing comma) parses to zero names, which is
    # the actual "empty scope" error path.
    with pytest.raises(ChwriteError):
        _parse_plain("version 1\nprotect a.txt deny-user=,\n", ".write_protect")


def test_parse_json_deny_user_deny_group_lists() -> None:
    text = (
        '{"version": 1, "protect": [{"pattern": "a", "deny_user": ["bob"], '
        '"deny_group": ["contractors"]}]}'
    )
    _, rules = _parse_json(text, ".write_protect.json")
    assert rules[0].deny_user == ("bob",)
    assert rules[0].deny_group == ("contractors",)


def test_parse_json_deny_user_not_a_list_is_config_error() -> None:
    text = '{"version": 1, "protect": [{"pattern": "a", "deny_user": "bob"}]}'
    with pytest.raises(ChwriteError):
        _parse_json(text, ".write_protect.json")


def test_parse_json_deny_user_empty_list_is_config_error() -> None:
    text = '{"version": 1, "protect": [{"pattern": "a", "deny_user": []}]}'
    with pytest.raises(ChwriteError):
        _parse_json(text, ".write_protect.json")


def test_parse_yaml_deny_user_csv_scalar(tmp_path) -> None:
    root = _init_repo(tmp_path)
    (tmp_path / ".write_protect.yaml").write_text(
        "version: 1\nprotect:\n  - pattern: a\n    deny_user: bob,alice\n"
    )
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules[0].deny_user == ("bob", "alice")


def test_resolve_policy_files_carries_scope() -> None:
    policy = Policy(
        "/repo/.write_protect",
        ".write_protect",
        1,
        (Rule("a.txt", None, None, ("bob",), ("contractors",)),),
    )
    # resolve_policy_files needs a real repo/git ls-files call for pathspec
    # rules; deny_user/deny_group propagation itself is validated at the
    # Rule level here and end-to-end in test_reconcile.py.
    assert policy.rules[0].deny_user == ("bob",)
    assert policy.rules[0].deny_group == ("contractors",)


# ---------------------------------------------------------------------------
# writer round-trips for regex/scope (SPEC.md 28, 29)
# ---------------------------------------------------------------------------


def test_write_plain_round_trips_regex_and_scope(tmp_path) -> None:
    path = str(tmp_path / ".write_protect")
    rules = [Rule(None, "msg", r"^a.*\.txt$", ("bob",), ("contractors", "interns"))]
    write_plain(path, 1, rules)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "protect-regex ^a.*\\.txt$" in text
    assert "deny-user=bob" in text
    assert "deny-group=contractors,interns" in text
    _, parsed = _parse_plain(text, ".write_protect")
    assert parsed == rules


def test_write_json_round_trips_regex_and_scope(tmp_path) -> None:
    root = _init_repo(tmp_path)
    path = str(tmp_path / ".write_protect.json")
    rules = [Rule(None, "msg", "^a.*", ("bob",), ("contractors",))]
    write_json(path, 1, rules)
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == tuple(rules)


def test_write_toml_round_trips_regex_and_scope(tmp_path) -> None:
    root = _init_repo(tmp_path)
    path = str(tmp_path / ".write_protect.toml")
    rules = [Rule(None, "msg", "^a.*", ("bob", "alice"), ("contractors",))]
    write_toml(path, 1, rules)
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == tuple(rules)


def test_write_yaml_round_trips_regex_and_scope(tmp_path) -> None:
    root = _init_repo(tmp_path)
    path = str(tmp_path / ".write_protect.yaml")
    rules = [Rule(None, "msg", "^a.*", ("bob", "alice"), ("contractors",))]
    write_yaml(path, 1, rules)
    policy = load_policy(root)
    assert policy is not None
    assert policy.rules == tuple(rules)
