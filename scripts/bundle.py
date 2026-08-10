#!/usr/bin/env python3
"""Bundle src/chwrite/* into a single-file chwrite.py (SPEC.md section 26.1).

Stdlib only (this script is itself part of the "no external libraries"
world, even though it's a dev-time tool). Concatenates the package's
modules in a fixed, hand-declared dependency order (no import cycles
exist between them, so this is simpler and more auditable than computing
a topological sort at build time), strips each module's docstring and
intra-package imports, hoists external stdlib imports (deduplicated) to
the top, and appends a `python chwrite.py` entrypoint.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "chwrite"
OUTPUT = REPO_ROOT / "chwrite.py"

# Fixed dependency order: each module here only imports from modules
# earlier in this list (verified by hand; there are no import cycles).
MODULE_ORDER = [
    "errors.py",
    "gitutil.py",
    "policy_yaml.py",
    "policy.py",
    "state.py",
    "backends/posix_generic.py",
    "backends/macos.py",
    "backends/linux.py",
    "backends/windows.py",
    "backends/unknown.py",
    "backends/__init__.py",
    "reconcile.py",
    "claude_hook.py",
    "hooks.py",
    "diagnostics.py",
    "cli.py",
]

FUTURE_IMPORT = "from __future__ import annotations"


def _strip_docstring(source: str) -> str:
    """Drop a module's leading docstring (the bundle gets one combined
    header instead of N stray string-literal expression statements)."""
    tree = ast.parse(source)
    first = tree.body[0] if tree.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        lines = source.splitlines(keepends=True)
        assert first.end_lineno is not None
        del lines[first.lineno - 1 : first.end_lineno]
        return "".join(lines)
    return source


def _extract_and_strip_imports(source: str) -> tuple[str, list[str]]:
    """Remove top-level import statements, returning (body, hoisted_lines).

    Intra-package imports (chwrite.* / relative) are dropped entirely
    rather than hoisted, since bundling already puts every symbol from
    every module into one flat namespace. `from __future__ import
    annotations` is dropped here too; the caller emits it exactly once,
    as the very first statement of the bundle (required by the language).
    """
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    hoisted: list[str] = []
    spans: list[tuple[int, int]] = []
    for node in tree.body:
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        assert node.end_lineno is not None
        start, end = node.lineno - 1, node.end_lineno
        spans.append((start, end))
        module = getattr(node, "module", None)
        is_intra_package = isinstance(node, ast.ImportFrom) and (
            node.level > 0 or (module or "").startswith("chwrite")
        )
        is_future = isinstance(node, ast.ImportFrom) and module == "__future__"
        if not is_intra_package and not is_future:
            hoisted.append("".join(lines[start:end]).rstrip("\n"))
    for start, end in sorted(spans, reverse=True):
        del lines[start:end]
    return "".join(lines), hoisted


def _read_module(rel_path: str) -> tuple[str, list[str]]:
    source = (SRC / rel_path).read_text(encoding="utf-8")
    source = _strip_docstring(source)
    body, hoisted = _extract_and_strip_imports(source)
    return body.strip("\n"), hoisted


def _current_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, check=False
        )
    except FileNotFoundError:
        return "unknown"
    return proc.stdout.decode().strip() if proc.returncode == 0 else "unknown (no commits yet)"


def build() -> str:
    """Render the full bundled chwrite.py source as a string."""
    all_hoisted: list[str] = []
    sections: list[str] = []
    for rel_path in MODULE_ORDER:
        body, hoisted = _read_module(rel_path)
        for line in hoisted:
            if line not in all_hoisted:
                all_hoisted.append(line)
        if body:
            banner = f"# --- {rel_path} " + "-" * max(1, 60 - len(rel_path))
            sections.append(f"{banner}\n\n{body}\n")

    header = (
        "#!/usr/bin/env python3\n"
        '"""chwrite - GENERATED, do not hand-edit.\n\n'
        "Regenerate with `just build` (or `python3 scripts/bundle.py`) after\n"
        "changing anything under src/chwrite/. This is the single-file,\n"
        "stdlib-only distributable artifact described in SPEC.md section 19;\n"
        "section 26 explains why the maintained source is a package instead.\n\n"
        f"Generated from src/chwrite/ at commit {_current_commit()}.\n"
        '"""\n\n'
        f"{FUTURE_IMPORT}\n\n"
    )
    imports_block = "\n".join(all_hoisted) + "\n\n" if all_hoisted else ""
    body_block = "\n\n".join(sections)
    footer = '\n\nif __name__ == "__main__":\n    sys.exit(main())\n'
    return header + imports_block + body_block + footer


def main() -> int:
    content = build()
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    try:
        OUTPUT.chmod(0o755)
    except PermissionError:
        # Not our file to chmod (e.g. it's owned by a different user on a
        # shared checkout - common when multiple agents/users build from
        # the same working tree). The content is still written correctly;
        # losing the executable bit here is a non-fatal cosmetic issue,
        # not a reason to fail the build.
        print(f"note: could not chmod {OUTPUT} (owned by another user?); leaving permissions as-is")
    print(f"wrote {OUTPUT} ({content.count(chr(10)) + 1} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
