# Installing chwrite

**Right now, exactly one method actually works for an end user: git clone.**
Every package manager below has real, tested packaging code in this repo
(see [`packaging/README.md`](./packaging/README.md) for the full
maintainer-facing status), but none has been published to its real
registry/tap/AUR yet - that's a one-time step only the project owner can do
per surface (create an account, link a token, create a tap repo). This page
will be updated as each one goes live; until then, the commands below for
the not-yet-live surfaces are what *will* work, not what works today.

| Method | Status | Command |
|---|---|---|
| git clone | **Works now** | see below |
| pip / pipx | Not published | `pip install chwrite` |
| npm | Not published | `npm install -g chwrite` |
| Homebrew | Not published | `brew install davidawad/chwrite/chwrite` |
| AUR | Not published | `yay -S chwrite` (or any AUR helper) |
| apt / dpkg | Not published | `.deb` attached to each GitHub Release once tagged; `sudo dpkg -i chwrite_*.deb` |
| winget | Not submitted | `winget install ChwriteProject.Chwrite` |

chwrite is two binaries, not one (see [`SPEC.md`](./SPEC.md) section 32):
**`chwrite`** is the everyday hot path (`apply`/`lock`/`unlock`/`status`/
`verify`/etc. - everything that only touches the current repo). **`chwrite-setup`**
is a separate, one-time-per-machine binary (`install`/`uninstall` - the only
two commands that touch global git config or your home directory). Every
install method below ships both.

## git clone (works today)

```bash
git clone https://github.com/davidawad/chwrite
cd chwrite
python3 chwrite-setup.py install
```

Requires Python 3.11+ and `git` on PATH. No root/admin privileges. This
installs a global git hook dispatcher and points `core.hooksPath` at it -
see the main [`README.md`](./README.md#install) for exactly what that does
and how to opt out (`--claude-hook`, `--force`, uninstalling).

After this, the `chwrite`/`chwrite.cmd` launchers in the cloned repo (and
`chwrite`/`chwrite status` from any directory, once installed) work
normally.

## pip / pipx

```bash
pip install chwrite      # or: pipx install chwrite
```

Installs the real `src/chwrite/` package with a `chwrite` console-script
entry point - not the bundled single-file artifact, an independent, equally
valid install path. Verified locally end-to-end (build -> install -> run);
not yet published to PyPI. **Blocked on:** the project owner linking a PyPI
Trusted Publisher to this repo (one-time, no token to leak - see
`packaging/README.md`'s "pip / pipx" section).

## npm

```bash
npm install -g chwrite
```

A thin Node shim (the only JS in the package) that execs the bundled
`chwrite.py` against whatever `python3`/`python`/`py` is on your PATH - no
runtime npm dependencies, no postinstall download step. Verified locally
end-to-end (`npm pack` -> install -> run); package name `chwrite` confirmed
available on the real registry. **Blocked on:** the project owner running
`npm login` and adding an `NPM_TOKEN` CI secret.

## Homebrew

```bash
brew install davidawad/chwrite/chwrite
```

Installs `chwrite.py` into `libexec` with a generated `bin/chwrite` wrapper
pinned to the formula's own Python. Formula content validated as far as
possible without a real tap (`ruby -c` clean; install/wrapper logic
exercised manually step-by-step since sandboxed network access blocked a
real `brew install`/`brew audit` run here). **Blocked on:** the project
owner creating a `homebrew-chwrite` tap repo (Homebrew requires taps to be
their own repo) and filling in a real release tarball's sha256.

## AUR (Arch Linux)

```bash
yay -S chwrite      # or paru, or plain makepkg + your AUR helper of choice
```

`PKGBUILD` installs `chwrite.py` + the `chwrite` POSIX shim into
`/usr/bin`. Validated as far as possible without a real Arch environment.
**Blocked on:** the project owner creating an AUR account/SSH key and
pushing the package for the first time.

## apt / dpkg (Debian, Ubuntu, etc.)

```bash
# once a release exists:
curl -LO https://github.com/davidawad/chwrite/releases/latest/download/chwrite_1.0.0-1_all.deb
sudo dpkg -i chwrite_1.0.0-1_all.deb
```

Full `debian/` control tree written (`debhelper-compat 13`, native source
format). CI attaches a real built `.deb` to every GitHub Release
automatically once tags are pushed - no hosted apt repo or PPA needed for
this to work, just a direct `.deb` download. **Blocked on:** nothing beyond
this repo actually being tagged/released with the workflow enabled - the
lightest lift of the six.

## winget (Windows)

```powershell
winget install ChwriteProject.Chwrite
```

Manifest drafted (`packaging/winget/`) using a `zip` installer with
`NestedInstallerType: portable`, since chwrite ships as `chwrite.py` +
`chwrite.cmd` rather than a compiled installer - genuinely the roughest
packaging fit of the six (see `packaging/README.md`'s "winget" section for
the full reasoning). **Blocked on:** a real GitHub release providing the
installer zip, then a manual first-time submission PR against
`microsoft/winget-pkgs` (winget has no self-hosted tap/repo concept -
every package lives in that one community repo).

## After installing, either way

```bash
cd your-repo
chwrite init                     # creates .write_protect
chwrite add package-lock.json
git add .write_protect && git commit -m "protect lockfile"
```

See the main [`README.md`](./README.md) for the full command reference and
[`SPEC.md`](./SPEC.md) for the complete design.
