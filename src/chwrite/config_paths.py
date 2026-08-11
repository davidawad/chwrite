"""Per-user chwrite config directory (SPEC.md section 6, 32.1).

Split out of hooks.py so both binaries can share it: `chwrite doctor`
(diagnostics.py) needs to *report* the install path without pulling in
hooks.py's install/uninstall logic, which lives exclusively in the
separate chwrite-setup binary (SPEC.md section 32).
"""

from __future__ import annotations

import os

from chwrite.backends import PLATFORM
from chwrite.errors import ChwriteError


def config_dir() -> str:
    """Per-user chwrite install directory (SPEC.md section 6)."""
    if PLATFORM == "windows":
        base = os.environ.get("APPDATA")
        if not base:
            raise ChwriteError("%APPDATA% is not set", 2)
        return os.path.join(base, "chwrite")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "chwrite")
