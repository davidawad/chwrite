# chwrite (npm)

npm wrapper around [chwrite](../../README.md) - a dependency-free CLI that
lets a Git repository declare files that should not be modified.

This package ships **no JavaScript implementation**. `bin/chwrite.js` is a
thin, dependency-free shim (Node stdlib only, no `dependencies` in
`package.json`) that finds a `python3`/`python` (or `py` on Windows)
interpreter on `PATH` and execs the bundled `chwrite.py` against it,
forwarding argv and the child's exit code. There is no download-at-install
step and no postinstall script - everything needed ships inside the
tarball.

## Requirements

Python 3.11+ must be installed and discoverable on `PATH` as `python3`,
`python`, or (Windows) the `py` launcher. This package does not vendor a
Python interpreter.

## `chwrite.py` is a vendored copy, not a symlink

`chwrite.py` in this directory is a **committed copy** of the repo-root
generated bundle (`SPEC.md` sections 19/26), not a live symlink - npm
tarballs cannot ship symlinks portably, and `files` entries must be real
files. **This copy must be refreshed from the repo-root `chwrite.py` on
every release** (`cp ../../chwrite.py packaging/npm/chwrite.py`, after
confirming the repo-root bundle is not stale relative to `src/chwrite/` -
see `just build-check`). A stale copy here is a real bug: the npm package
would silently ship an older version than PyPI/Homebrew/etc. This is
exactly the kind of drift `.github/workflows/release.yml` should assert
(diff this file against the repo-root artifact) before publishing - see
`packaging/README.md`.

## Usage

```bash
npm install -g chwrite
chwrite status
```
