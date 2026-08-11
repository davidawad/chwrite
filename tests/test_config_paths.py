"""config_dir() tests (SPEC.md section 6, 32.1).

Split out of test_hooks.py when config_dir() moved to its own module so
both chwrite and chwrite-setup can share it without pulling install/
uninstall logic into the hot-path binary (SPEC.md section 32).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chwrite import config_paths
from chwrite.errors import ChwriteError


def test_config_dir_uses_xdg_config_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_paths.config_dir() == str(tmp_path / "xdg" / "chwrite")


def test_config_dir_falls_back_to_home_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))
    assert config_paths.config_dir() == str(tmp_path / ".config" / "chwrite")


def test_config_dir_windows_uses_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_paths, "PLATFORM", "windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    assert config_paths.config_dir() == str(tmp_path / "AppData" / "Roaming" / "chwrite")


def test_config_dir_windows_errors_without_appdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config_paths, "PLATFORM", "windows")
    monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(ChwriteError):
        config_paths.config_dir()
