"""Git hook dispatcher install/uninstall tests (SPEC.md 6-9, 27).

Never touches the real machine's global git config or ~/.config/chwrite:
`config_dir()` is redirected into tmp_path via XDG_CONFIG_HOME, and every
`git config --global ...` call chwrite.hooks makes is intercepted by a
fake subprocess.run so these tests cannot mutate this dev machine's actual
git configuration.
"""

from __future__ import annotations

import subprocess
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from chwrite import hooks
from chwrite.backends.posix_generic import protect_posix
from chwrite.errors import ChwriteError
from chwrite.state import load_state, save_state, state_paths

_REAL_SUBPROCESS_RUN = subprocess.run


class FakeGitConfig:
    """Stands in for `git config --global ...` calls against core.hooksPath.

    Only intercepts the exact `core.hooksPath` get/set/unset invocations -
    everything else (e.g. `git init`, `git rev-parse --show-toplevel` used
    by other tests sharing this process's monkeypatched subprocess.run) is
    passed through to the real subprocess.run, since patching
    `chwrite.hooks.subprocess.run` patches the subprocess module object
    itself (hooks.py does `import subprocess`, not `from subprocess import
    run`), which is process-global, not module-local.
    """

    def __init__(self, initial: str | None = None) -> None:
        self.hooks_path: str | None = initial
        self.set_calls: list[list[str]] = []
        self.unset_calls: list[list[str]] = []

    def run(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if args[:4] == ["git", "config", "--global", "--get"] and args[4] == "core.hooksPath":
            if self.hooks_path is None:
                return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(args, 0, stdout=self.hooks_path.encode(), stderr=b"")
        if args[:4] == ["git", "config", "--global", "--unset"] and args[4] == "core.hooksPath":
            self.unset_calls.append(args)
            self.hooks_path = None
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        if args[:3] == ["git", "config", "--global"] and args[3] == "core.hooksPath":
            self.set_calls.append(args)
            self.hooks_path = args[4]
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        return _REAL_SUBPROCESS_RUN(args, **kwargs)


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg_home = tmp_path / "xdg-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    return cfg_home / "chwrite"


@pytest.fixture
def fake_chwrite_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Makes _find_chwrite_py() (SPEC.md 32.2) deterministically resolve to
    a fake sibling chwrite.py under tmp_path, never a real `chwrite` on this
    test-running machine's PATH - matches the old self-referential-copy
    behavior these tests were written against: is_on_path=False, dest_py
    ends up under isolated_config_dir."""
    monkeypatch.setattr(hooks.shutil, "which", lambda _name: None)
    fake_source = tmp_path / "source" / "chwrite.py"
    fake_source.parent.mkdir(parents=True, exist_ok=True)
    fake_source.write_text("#!/usr/bin/env python3\n# fake chwrite.py for tests\n")
    monkeypatch.setattr(hooks, "_find_chwrite_py", lambda: (str(fake_source), False))
    return fake_source


def _init_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return str(tmp_path)


# ---------------------------------------------------------------------------
# _hook_script
# ---------------------------------------------------------------------------


def test_hook_script_pre_commit_runs_verify() -> None:
    script = hooks._hook_script("pre-commit", "/opt/chwrite/chwrite.py")
    assert "verify" in script
    assert "apply" not in script
    assert script.startswith("#!/bin/sh\n")


def test_hook_script_post_checkout_runs_apply_quiet() -> None:
    script = hooks._hook_script("post-checkout", "/opt/chwrite/chwrite.py")
    assert "apply --quiet" in script


def test_hook_script_pre_push_runs_verify() -> None:
    script = hooks._hook_script("pre-push", "/opt/chwrite/chwrite.py")
    assert "verify" in script


# ---------------------------------------------------------------------------
# _find_chwrite_py (SPEC.md 32.2)
# ---------------------------------------------------------------------------


def test_find_chwrite_py_prefers_path_over_sibling_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks.shutil, "which", lambda name: "/usr/local/bin/chwrite")
    sibling = tmp_path / "chwrite.py"
    sibling.write_text("# unused - PATH takes precedence")
    monkeypatch.setattr(hooks.os.path, "realpath", lambda _p: str(tmp_path / "hooks.py"))
    ref, is_on_path = hooks._find_chwrite_py()
    assert ref == "/usr/local/bin/chwrite"
    assert is_on_path is True


def test_find_chwrite_py_falls_back_to_sibling_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks.shutil, "which", lambda name: None)
    sibling = tmp_path / "chwrite.py"
    sibling.write_text("# fake")
    monkeypatch.setattr(hooks.os.path, "realpath", lambda _p: str(tmp_path / "hooks.py"))
    ref, is_on_path = hooks._find_chwrite_py()
    assert ref == str(sibling)
    assert is_on_path is False


def test_find_chwrite_py_raises_when_neither_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks.shutil, "which", lambda name: None)
    monkeypatch.setattr(hooks.os.path, "realpath", lambda _p: str(tmp_path / "hooks.py"))
    with pytest.raises(ChwriteError) as exc_info:
        hooks._find_chwrite_py()
    assert exc_info.value.code == 2
    assert "no 'chwrite' on PATH" in str(exc_info.value)


# ---------------------------------------------------------------------------
# cmd_install
# ---------------------------------------------------------------------------


def test_cmd_install_uses_chwrite_already_on_path(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path
) -> None:
    fake = FakeGitConfig(initial=None)
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)
    monkeypatch.setattr(hooks, "_find_chwrite_py", lambda: ("/usr/local/bin/chwrite", True))

    code = hooks.cmd_install(Namespace(force=False, claude_hook=False))
    assert code == 0

    # No copy made into the config dir when chwrite is already on PATH.
    assert not (isolated_config_dir / "chwrite.py").exists()
    for name in hooks.HOOK_NAMES:
        hook_path = isolated_config_dir / "hooks" / name
        assert hook_path.is_file()
        assert "exec chwrite " in hook_path.read_text()


def test_cmd_install_writes_hooks_and_configures_git(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path, fake_chwrite_source: Path
) -> None:
    fake = FakeGitConfig(initial=None)
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)

    code = hooks.cmd_install(Namespace(force=False, claude_hook=False))
    assert code == 0

    dest_py = isolated_config_dir / "chwrite.py"
    assert dest_py.is_file()
    for name in hooks.HOOK_NAMES:
        hook_path = isolated_config_dir / "hooks" / name
        assert hook_path.is_file()
        assert hook_path.stat().st_mode & 0o111  # executable

    assert fake.hooks_path == str(isolated_config_dir / "hooks")
    assert len(fake.set_calls) == 1


def test_cmd_install_refuses_to_overwrite_existing_hooks_path(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path, fake_chwrite_source: Path
) -> None:
    fake = FakeGitConfig(initial="/some/other/hooks/dir")
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)

    with pytest.raises(ChwriteError) as exc_info:
        hooks.cmd_install(Namespace(force=False, claude_hook=False))
    assert exc_info.value.code == 2
    assert not fake.set_calls


def test_cmd_install_force_overwrites_existing_hooks_path(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path, fake_chwrite_source: Path
) -> None:
    fake = FakeGitConfig(initial="/some/other/hooks/dir")
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)

    code = hooks.cmd_install(Namespace(force=True, claude_hook=False))
    assert code == 0
    assert fake.hooks_path == str(isolated_config_dir / "hooks")


def test_cmd_install_idempotent_when_hooks_path_already_correct(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path, fake_chwrite_source: Path
) -> None:
    fake = FakeGitConfig(initial=str(isolated_config_dir / "hooks"))
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)

    code = hooks.cmd_install(Namespace(force=False, claude_hook=False))
    assert code == 0


def test_cmd_install_with_claude_hook_writes_settings_json(
    monkeypatch: pytest.MonkeyPatch,
    isolated_config_dir: Path,
    tmp_path: Path,
    fake_chwrite_source: Path,
) -> None:
    fake = FakeGitConfig(initial=None)
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)
    repo_dir = tmp_path / "myrepo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)

    code = hooks.cmd_install(Namespace(force=False, claude_hook=True))
    assert code == 0
    assert (repo_dir / ".claude" / "settings.json").is_file()


# ---------------------------------------------------------------------------
# cmd_uninstall
# ---------------------------------------------------------------------------


def test_cmd_uninstall_removes_matching_hooks_path(
    monkeypatch: pytest.MonkeyPatch,
    isolated_config_dir: Path,
    tmp_path: Path,
    fake_chwrite_source: Path,
) -> None:
    fake = FakeGitConfig(initial=None)
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)
    hooks.cmd_install(Namespace(force=False, claude_hook=False))
    assert fake.hooks_path == str(isolated_config_dir / "hooks")

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)

    code = hooks.cmd_uninstall(Namespace())
    assert code == 0
    assert fake.hooks_path is None
    assert not isolated_config_dir.exists()


def test_cmd_uninstall_leaves_unrelated_hooks_path_alone(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path, tmp_path: Path, capsys
) -> None:
    fake = FakeGitConfig(initial="/some/other/hooks/dir")
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    monkeypatch.chdir(outside)

    code = hooks.cmd_uninstall(Namespace())
    assert code == 0
    assert fake.hooks_path == "/some/other/hooks/dir"
    assert not fake.unset_calls
    assert "points elsewhere" in capsys.readouterr().out


def test_cmd_uninstall_unlocks_locked_files_in_current_repo(
    monkeypatch: pytest.MonkeyPatch, isolated_config_dir: Path, tmp_path: Path
) -> None:
    fake = FakeGitConfig(initial=None)
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)

    root = _init_repo(tmp_path)
    monkeypatch.chdir(root)
    target = Path(root) / "protected.txt"
    target.write_text("x")
    # Use protect_posix directly (chmod-only) rather than the real PLATFORM
    # backend that cmd_uninstall's unprotect_path dispatches through, so
    # this stays a chmod round-trip on every OS this suite might run on;
    # unprotect_macos/etc. only act on their own "backend" tag (see
    # entry["backend"] below), so calling the real dispatcher to *reverse*
    # this chmod-only state is still safe - it just restores original_mode.
    result = protect_posix(str(target), hard=False)
    state = load_state(root)
    state["files"]["protected.txt"] = {
        "backend": result["backend"],
        "level": result["level"],
        "original_mode": 0o644,
        "locked": True,
        "source": "policy",
        "message": "m",
        "hard": False,
    }
    save_state(root, state)

    code = hooks.cmd_uninstall(Namespace())
    assert code == 0
    assert (target.stat().st_mode & 0o200) != 0  # write bit restored

    state_dir, _ = state_paths(root)
    assert not Path(state_dir).exists()


def test_cmd_uninstall_outside_any_repo_still_removes_config_dir(
    monkeypatch: pytest.MonkeyPatch,
    isolated_config_dir: Path,
    tmp_path: Path,
    fake_chwrite_source: Path,
) -> None:
    fake = FakeGitConfig(initial=None)
    monkeypatch.setattr(hooks.subprocess, "run", fake.run)
    hooks.cmd_install(Namespace(force=False, claude_hook=False))

    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    monkeypatch.chdir(outside)

    code = hooks.cmd_uninstall(Namespace())
    assert code == 0
    assert not isolated_config_dir.exists()
