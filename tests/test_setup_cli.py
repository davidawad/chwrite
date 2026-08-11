"""setup_cli.py argparse wiring + main() error mapping tests (SPEC.md 32).

install/uninstall's actual behavior is covered in test_hooks.py against
hooks.cmd_install/cmd_uninstall directly - these tests only exercise
setup_cli's own dispatch/argparse/error-mapping layer, mocking
cmd_install/cmd_uninstall out so they never touch real global git config
or ~/.config/chwrite.
"""

from __future__ import annotations

import argparse

import pytest

from chwrite import setup_cli
from chwrite.errors import ChwriteError


def test_build_parser_both_subcommands_registered() -> None:
    parser = setup_cli.build_parser()
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    assert set(sub_action.choices.keys()) == {"install", "uninstall"}


def test_main_requires_a_subcommand() -> None:
    with pytest.raises(SystemExit):
        setup_cli.main([])


def test_main_help_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        setup_cli.main(["--help"])
    assert exc_info.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_main_dispatches_install(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []
    monkeypatch.setattr(setup_cli, "cmd_install", lambda args: calls.append(args) or 0)
    code = setup_cli.main(["install"])
    assert code == 0
    assert len(calls) == 1
    assert calls[0].force is False
    assert calls[0].claude_hook is False


def test_main_dispatches_install_with_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []
    monkeypatch.setattr(setup_cli, "cmd_install", lambda args: calls.append(args) or 0)
    code = setup_cli.main(["install", "--force", "--claude-hook"])
    assert code == 0
    assert calls[0].force is True
    assert calls[0].claude_hook is True


def test_main_dispatches_uninstall(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []
    monkeypatch.setattr(setup_cli, "cmd_uninstall", lambda args: calls.append(args) or 0)
    code = setup_cli.main(["uninstall"])
    assert code == 0
    assert len(calls) == 1


def test_main_maps_chwrite_error_to_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_error(_args: argparse.Namespace) -> int:
        raise ChwriteError("no chwrite found", 2)

    monkeypatch.setattr(setup_cli, "cmd_install", raise_error)
    code = setup_cli.main(["install"])
    assert code == 2
    assert "chwrite-setup:" in capsys.readouterr().err


def test_main_maps_keyboard_interrupt_to_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_interrupt(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(setup_cli, "cmd_uninstall", raise_interrupt)
    code = setup_cli.main(["uninstall"])
    assert code == 130
