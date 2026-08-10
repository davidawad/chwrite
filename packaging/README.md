# Packaging & Distribution

Tracks the status of every packaging surface described in `SPEC.md` section 31.
Per section 31's honesty rule: a surface is only marked **live** once every
external, human-only, one-time setup step (account, tap/repo creation,
credential) it depends on has actually been completed — not merely once the
packaging files exist in this repo and pass local validation.

| Surface   | Files                  | Status | Blocking one-time step(s) |
|-----------|-------------------------|--------|----------------------------|
| pip / pipx | `pyproject.toml` (`[project.scripts]`) | Wired, not live | PyPI project + Trusted Publisher link (one-time, PyPI account holder only) |
| npm       | `packaging/npm/`        | Wired, not live | `npm login` + `NPM_TOKEN` repo secret; package name `chwrite` confirmed available (verified 404 on registry) |
| Homebrew  | `packaging/homebrew/`   | Wired, not live | Create `homebrew-chwrite` tap repo; real GitHub release; sha256 fill-in (see below) |
| winget    | `packaging/winget/`     | Drafted, not submitted | No GitHub remote/release yet (real InstallerUrl + sha256); first submission is a manual `wingetcreate submit` PR against `microsoft/winget-pkgs` (see below) |
| AUR       | `packaging/aur/`       | Wired, not live | GitHub remote + release; sha256 fill-in; AUR account/SSH key + push to aur.archlinux.org |
| Debian/apt | `packaging/debian/`   | Wired, not live | GitHub remote + release (CI can attach a built .deb as a release asset with zero extra infra); real apt repo/PPA optional and separate |

(Other surfaces - pip/PyPI, npm - are tracked by
whichever agent/commit adds their section; append below, don't overwrite this
table's other rows.)

## pip / pipx (PyPI)

**Status: wired, verified locally, not published.**

`pyproject.toml` has a `[project.scripts]` entry (`chwrite = "chwrite.cli:main"`)
— `src/chwrite/cli.py` already had a `main(argv=None) -> int` in exactly the
right shape, no wrapper needed. Verified for real:

```bash
uv build                                                    # -> dist/chwrite-1.0.0-py3-none-any.whl + sdist
uv venv --seed /tmp/verify && /tmp/verify/bin/pip install dist/chwrite-1.0.0-py3-none-any.whl
/tmp/verify/bin/chwrite --help                              # real, working CLI
```

This installs the actual `src/chwrite/` package (not the bundled single-file
artifact) — an independent, equally-valid install path from the curl'd
`chwrite.py` (SPEC.md section 31.1).

**Blocking one-time step:** publishing requires a PyPI project for `chwrite`
and a Trusted Publisher link (OIDC from this repo's GitHub Actions — no
long-lived token needed) configured by whoever owns the PyPI account. Until
that exists, `.github/workflows/release.yml`'s PyPI job is wired but will
simply not run (missing secret/config = skip, not fail, per SPEC.md 31.7).

## npm

**Status: wired, verified locally, not published.**

`packaging/npm/` — `package.json` (name `chwrite`; confirmed available on the
real registry: `npm view chwrite` returns 404/not found, not an existing
package) with a `bin/chwrite.js` shim. This is the *only* JS in the package:
its sole job is to locate a `python3`/`python`/`py` interpreter on PATH and
`execFileSync` the bundled `chwrite.py` (shipped in the tarball via `files`),
passing through argv and the child's exit code. No runtime npm dependencies,
no postinstall download step — the whole point was avoiding exactly that
network/supply-chain surface (SPEC.md section 31.2). `chwrite.py` inside
`packaging/npm/` is a **copy**, not a live symlink — it must be refreshed
from the repo-root bundle on every release (the release workflow does this
automatically, see SPEC.md 31.7).

Verified for real:

```bash
cd packaging/npm && npm pack                                # -> chwrite-1.0.0.tgz, 4 files, 27.9kB
npm install -g --prefix /tmp/verify ./chwrite-1.0.0.tgz
/tmp/verify/bin/chwrite --help                               # real, working CLI
```

**Blocking one-time step:** publishing requires `npm login` under an account
that owns the `chwrite` package name, and an `NPM_TOKEN` repo secret for CI
to publish non-interactively. Until that exists, the npm publish job is
wired but will not run.

## Homebrew

**Status: formula written and validated as far as possible without a real
release. This is NOT "installable via `brew install`" yet.**

### What exists

`packaging/homebrew/chwrite.rb` - a Homebrew formula that:

- Downloads the tagged GitHub release source tarball
  (`https://github.com/davidawad/chwrite/archive/refs/tags/vX.Y.Z.tar.gz`).
- Verifies its `sha256`.
- Depends on `python@3.12` (chwrite.py is stdlib-only per SPEC.md section 1/19;
  3.11+ is required only for optional `tomllib`-based TOML policy support,
  SPEC.md section 24.1).
- Installs `chwrite.py` into `libexec` and generates a `bin/chwrite` wrapper
  script that execs this formula's own pinned `python@3.12` against the
  libexec copy (see the formula's own comments for why this was chosen over
  adapting the repo's `chwrite` POSIX shim - short version: Homebrew already
  pins both interpreter and script to known paths at install time, so a
  generated wrapper with a hardcoded interpreter path is fewer moving parts
  than a shim designed for `$PATH`-searching, non-packaged installs).
- Has a `test do` block that runs `chwrite --help`.

### Two placeholders that MUST be filled in before this is usable

`packaging/homebrew/chwrite.rb` currently has:

1. `davidawad/chwrite` in `homepage`/`url` - this repo has no
   GitHub remote yet (`git remote -v` shows a local filesystem path). Replace
   once one exists.
2. `PLACEHOLDER_SHA256` in `sha256` - a real value can only be computed
   against a real, published release tarball:

   ```bash
   curl -L -o chwrite-X.Y.Z.tar.gz \
     https://github.com/davidawad/chwrite/archive/refs/tags/vX.Y.Z.tar.gz
   shasum -a 256 chwrite-X.Y.Z.tar.gz
   ```

   Update this on every release (or via the CI automation described below,
   once it exists).

### The tap repo - a one-time manual step only a human can do

Homebrew requires taps to be their own repository, named `homebrew-<name>`.
There is no way for this repo's own CI to create that repo on its own behalf
(it requires a GitHub account/org decision and repo-creation permissions
outside this project's scope). A human must, once:

1. Create a new GitHub repository named `homebrew-chwrite` under the same
   account/org that owns the `chwrite` repo.
2. Add `Formula/chwrite.rb` to it (the current content of
   `packaging/homebrew/chwrite.rb`, with the two placeholders above filled in
   against a real tagged release).
3. Verify it locally: `brew tap <owner>/chwrite` (Homebrew resolves this to
   `github.com/<owner>/homebrew-chwrite`), then `brew install chwrite` /
   `brew audit --new chwrite` / `brew style chwrite`.

Once that tap repo exists and a deploy key or fine-grained PAT scoped to it is
configured as a repo secret, `.github/workflows/release.yml` (SPEC.md section
31.7) can auto-bump the tap's `Formula/chwrite.rb` `url`/`sha256` on every
tagged release - it still cannot create the tap repo itself.

### Local validation performed (this pass, no tap/release yet)

Environment: `brew` 6.0.15 and `ruby` 3.3.10 available locally.

- `ruby -c packaging/homebrew/chwrite.rb` → `Syntax OK`.
- `brew audit --new --formula` / `brew style` on the formula: **could not
  complete** - both invoke Homebrew's vendored `bundle install`, which in this
  environment hits a sandboxed-network 403 (`blocked-by-allowlist`) against
  `rubygems.org`. This reproduces even with `BUNDLE_FROZEN=true` forced,
  because the vendored gem set and its lockfile disagree on the installed
  `rubocop` version locally, forcing a spec-index refetch. This is an
  environment/network limitation, not a defect surfaced in the formula.
- `brew install --build-from-source` against a real local fake tarball
  (built from this repo's own `chwrite.py`/`chwrite`/`chwrite.cmd`, sha256
  computed for real): **could not complete either** - `/opt/homebrew/Cellar`
  is not writable by this run's OS user, and `formulae.brew.sh`/`ghcr.io`
  (needed to fetch the `python@3.12` dependency's bottle) are also behind the
  same sandboxed-network 403.
- Given both of the above, the `install`/`test do` block logic was instead
  validated by manually executing the exact same steps outside the `Formula`
  DSL: extract the fake tarball, copy `chwrite.py` into a `libexec`-equivalent
  dir, generate the wrapper script byte-for-byte as the formula's heredoc
  would, `chmod +x`, then run `chwrite --help` against it. This passed with
  exit code 0 and the expected `chwrite` usage output, confirming the
  install/wrapper/test logic is mechanically sound independent of the
  network/Cellar-permission issues above.

**Net: formula content and structure are believed correct and were
mechanically exercised as far as this sandbox allows. Full `brew audit
--new`/`brew style`/`brew install` against the real formula, and installability
via `brew tap`, remain unverified until (a) network access to
`formulae.brew.sh`/`ghcr.io`/`rubygems.org` and Cellar write access are
available in some environment, or (b) a human runs them directly, and (c) the
tap repo + real release described above exist.**

## winget (Windows)

**Status: manifest drafted, not submitted.** Nothing here is live yet.

### What exists

`packaging/winget/manifests/c/ChwriteProject/Chwrite/1.0.0/` contains a
three-file winget-pkgs manifest, laid out at the exact path winget-pkgs
itself uses (`manifests/<first-letter-lowercase>/<Publisher>/<Package>/<version>/`),
so it can be copied directly into a `microsoft/winget-pkgs` checkout when
the time comes:

* `ChwriteProject.Chwrite.yaml` - version manifest.
* `ChwriteProject.Chwrite.installer.yaml` - installer manifest.
* `ChwriteProject.Chwrite.locale.en-US.yaml` - default locale manifest.

`PackageIdentifier` is `ChwriteProject.Chwrite` (winget's `Publisher.PackageName`
convention).

### Why this is the roughest fit of the six packaging surfaces

Every other surface in this repo (pip, npm, Homebrew, AUR, Debian) has a
distribution mechanism that naturally matches "here is a script plus a
tiny launcher, put it somewhere on PATH." winget does not: its manifest
schema is built around real installer semantics - `msi`, `exe` (with a
silent-switch convention), `msix`/`appx`, `burn` bundles, or a single-file
`portable` executable. chwrite is none of those: it's `chwrite.py` (a
script, not a binary) plus `chwrite.cmd` (a batch launcher that shells out
to whatever `python`/`py` is on PATH). There is no compiled Windows
executable to hand winget.

Two `InstallerType` values were considered:

1. **`portable` (top-level, un-nested).** This is winget's mechanism for
   "just a single standalone file, no real installer, put it on PATH."
   It fits the *spirit* of chwrite well, but the top-level `portable`
   installer type expects the `InstallerUrl` to point at exactly one file.
   chwrite needs two files shipped together (`chwrite.py` +
   `chwrite.cmd`, since the launcher locates its sibling `chwrite.py` at
   runtime via `%~dp0`) - a single-file installer type can't express that
   without hiding one of the two files inside the other (not viable for a
   plain `.py`/`.cmd` pair). Rejected for this reason alone.

2. **`zip` with `NestedInstallerType: portable` (chosen).** winget can
   download and unpack an ordinary zip archive, then treat one or more of
   the unpacked files as a `portable`-type installer via
   `NestedInstallerFiles`. This is exactly the "just drop these files
   somewhere and expose a command" case winget added the nested-portable
   mechanism for. The manifest here packages `chwrite.py` and
   `chwrite.cmd` together inside one zip, and declares
   `NestedInstallerFiles: [{RelativeFilePath: chwrite.cmd,
   PortableCommandAlias: chwrite}]` - winget unpacks both files into its
   package directory and creates a `chwrite` command-alias symlink
   pointing at `chwrite.cmd` in its `Links` directory (which it adds to
   the user's `PATH`). Since Windows' default `PATHEXT` includes `.CMD`,
   typing `chwrite` at a `cmd.exe` or PowerShell prompt resolves to that
   symlink. `chwrite.cmd` already resolves `chwrite.py` as a sibling file
   in its own directory (`%~dp0chwrite.py`) before falling back to
   `%APPDATA%\chwrite\chwrite.py`, so it works unmodified once both files
   land together in winget's portable install directory - no changes to
   `chwrite.cmd` itself were needed for this to work.

### Known unknowns / what still needs real-Windows verification

* winget's `portable` (including nested-portable) installer type is
  primarily documented and exercised in the wild with compiled `.exe`
  files. Using a `.cmd` batch file as the `PortableCommandAlias` target
  is not a pattern this was written against a live example of - it should
  work given `PATHEXT` resolution, but has not been verified with real
  `winget install` on a Windows machine, only YAML-syntax-checked (see
  below). Flag this explicitly during the actual winget-pkgs PR review.
* chwrite additionally requires a Python 3.11+ interpreter already on
  `PATH` (`python` or `py`). winget has a `Dependencies.PackageDependencies`
  mechanism for declaring "install this other winget package first," but
  it was deliberately **not** used here: it would force a winget-managed
  Python install even for users who already have Python from python.org,
  the Microsoft Store, or elsewhere, and winget can't detect a
  non-winget-installed interpreter to skip it. `chwrite.cmd` already fails
  with a clear stderr message ("no Python interpreter found") rather than
  crashing opaquely if none is present, so the manifest instead documents
  the Python requirement in the locale manifest's `Description` and
  leaves interpreter provisioning to the user.
* `ManifestVersion: 1.6.0` (the winget-pkgs schema version used in these
  files) should be re-checked against whatever schema version
  `microsoft/winget-pkgs` actually requires at submission time - it drifts
  over time and this was not checked against a live copy of the schema.

### Validation performed

Local, syntax-only YAML validation (`python3 -c "import yaml; yaml.safe_load(...)"`)
against all three files - confirms they are well-formed YAML and parse
into the expected shape (correct top-level key counts, correctly nested
`Installers[0].NestedInstallerFiles[0]`, etc.). This is **not** validation
against winget's actual JSON Schema, and it is **not** `winget validate`
(a Windows-only tool that also checks installer reachability and hashes)
- neither is available in this environment. Full schema/behavioral
validation has not been performed and must happen on a Windows box (or via
winget-pkgs' own CI, which runs schema validation and installer sandboxing
on every PR) before submission.

### Submission process (once a real release exists)

winget has no self-hosted "tap" or "repo" concept the way Homebrew/AUR do
- every package's manifest lives inside the single community repository
[`microsoft/winget-pkgs`](https://github.com/microsoft/winget-pkgs), and
publishing means opening a pull request against that repo. There is no
way to make a winget package "live" without going through Microsoft's
repo and its automated + human review.

The standard tool for this is
[`wingetcreate`](https://github.com/microsoft/winget-create) (`wingetcreate
update` or `wingetcreate submit`), which can generate/update a manifest
from a release URL and open the PR on your behalf given a GitHub token.
This repo's release workflow (SPEC.md section 31.7) could run
`wingetcreate update` in CI once a real tagged GitHub release with a
Windows zip asset exists, automating version bumps for *subsequent*
releases. In practice, the **first** submission for a brand-new
`PackageIdentifier` (this one, `ChwriteProject.Chwrite`) is normally done
manually - a first-time package has to clear winget-pkgs' validation bot
and often a round of human moderator review before the identifier exists
in the repo at all, which isn't something worth fully automating until
after that initial PR has landed.

None of this has happened yet: there is no GitHub remote for this repo,
no tagged release with built artifacts, and therefore no real
`InstallerUrl`/`InstallerSha256` to put in the installer manifest (both
are placeholders in the manifest files, clearly marked). This section
will be updated once a submission is actually opened.

## AUR (Arch Linux)

**Status: PKGBUILD written and validated as far as possible without a real
Arch Linux environment or a real release. This is NOT submitted to the AUR
yet.**

### What exists

`packaging/aur/PKGBUILD` — an AUR package build script that:

- Downloads the tagged GitHub release source tarball
  (`https://github.com/davidawad/chwrite/archive/refs/tags/vX.Y.Z.tar.gz`).
- Verifies its `sha256sums`.
- Depends on `python` (Arch's package name for CPython 3; chwrite.py is
  stdlib-only per SPEC.md section 1/19).
- `check()` runs `python chwrite.py --help` as a build-time smoke test
  (mirrors the philosophy of `just build`'s `chwrite.py --help` check,
  SPEC.md section 26.1) — deliberately not the real pytest suite, which is
  dev-only tooling this package does not depend on at build or run time
  (SPEC.md section 26.2).
- `package()` installs `chwrite.py` and the existing `chwrite` POSIX shim
  together into `/usr/bin` via `install -Dm755`, plus `LICENSE` into
  `/usr/share/licenses/chwrite/` and `README.md` into
  `/usr/share/doc/chwrite/`.

No `.SRCINFO` is committed by hand. AUR requires a `.SRCINFO` alongside
every `PKGBUILD` submission, but it is a *generated* file
(`makepkg --printsrcinfo > .SRCINFO`) — committing a hand-written one risks
it silently drifting out of sync with the PKGBUILD it's supposed to
describe. Regenerate it immediately before every AUR push, from an actual
Arch environment (or an Arch container/CI image — `archlinux:base-devel` on
Docker Hub is the standard choice), never by hand.

### Two placeholders that MUST be filled in before this is usable

Same shape as the Homebrew formula's placeholders:

1. `davidawad/chwrite` in `url=`/`source=` — this repo has no
   GitHub remote yet. Replace once one exists.
2. `sha256sums=('REPLACE_WITH_REAL_SHA256_ONCE_RELEASE_TARBALL_EXISTS')` —
   only computable against a real, published release tarball:

   ```bash
   curl -sL "https://github.com/davidawad/chwrite/archive/refs/tags/v1.0.0.tar.gz" | sha256sum
   ```

   or, from an Arch machine with the file downloaded per `source=`, let
   `makepkg -g` compute and print the line for you.

Also note the `_srcdir="chwrite-$pkgver"` assumption inside the PKGBUILD:
GitHub codeload tarballs for tag `vX.Y.Z` extract to `<repo>-X.Y.Z` (the
leading `v` is stripped from the directory name, not the tag) — this
assumes `<GITHUB_REPO>` is literally `chwrite`. Update `_srcdir` if the real
repository is ever named differently.

### The AUR account/repo — a one-time manual step only a human can do

AUR packages are published by pushing to a personal `ssh://` git repo tied
to an AUR account's SSH key — there is no way for this project's own CI to
create that account or attach a key to it on a human's behalf. A human
must, once:

1. Create an AUR account at https://aur.archlinux.org/register/.
2. Add an SSH public key to that account (AUR account settings).
3. `git clone ssh://aur@aur.archlinux.org/chwrite.git` (this creates the
   package repo on first push if it doesn't exist yet — AUR auto-creates a
   repo the first time you push a valid `PKGBUILD`+`.SRCINFO` pair to a new
   package name).
4. Copy in `packaging/aur/PKGBUILD` (placeholders filled in against a real
   release) and a freshly generated `.SRCINFO`, commit, push.

**Ongoing maintainer responsibility, per AUR rules (not a one-time step):**
AUR packages must be kept up to date by their maintainer or they can be
flagged out-of-date / orphaned / disowned by other users or AUR admins.
Every future `chwrite` release needs its `pkgver`/`pkgrel`/`sha256sums`
bumped and re-pushed — this is not automatable purely from this repo's own
CI without that same one-time SSH key being available to it as a secret
(same pattern as the Homebrew tap bump described above; SPEC.md section
31.7 already scopes this: "a missing secret skips that job with a clear
log line, it never fails the whole release").

### Local validation performed (this pass, no Arch environment or release)

Environment: macOS-based sandbox. `makepkg`/`namcap` are Arch-specific
tools and are **not available** here (`command -v makepkg` /
`command -v namcap` both resolve to nothing) — this is a fundamentally
Arch-only toolchain, unlike Debian's `dpkg-dev` which is at least
theoretically installable cross-platform via Homebrew (see the Debian
section below for why even that didn't pan out in this sandbox).

What *was* verified, all against the real system `bash` (PKGBUILD is a
bash script, even though `makepkg` itself is unavailable):

- `bash -c 'source PKGBUILD; ...'` — the file parses as valid bash with
  zero syntax errors, and every variable (`pkgname`, `pkgver`, `pkgrel`,
  `arch`, `license`, `depends`, `source[0]`, `sha256sums[0]`) resolves to
  the expected value. `declare -F check package` confirms both functions
  are defined.
- A Python-based structural check confirms every field AUR/makepkg require
  (`pkgname pkgver pkgrel pkgdesc arch url license depends source
  sha256sums`) is present, `package()` exists, and brace-matching is sound
  in both `check()`/`package()` bodies.
- The `check()` function's actual command
  (`python chwrite.py --help`) was run for real against this repo's
  `chwrite.py` and confirmed to exit 0 with the expected usage output.

What was **not** and **cannot** be verified without a real Arch Linux
environment (or a real GitHub release, for the download/checksum half):

- `makepkg` itself (dependency resolution against a real `pacman` DB,
  actual `.tar.gz` download + extraction + `sha256sum` verification,
  fakeroot `package()` execution, `.pkg.tar.zst` production).
- `namcap` linting (naming conventions, missing dependencies detected via
  `ldd`/import scanning, `.install` script conventions — none of which
  apply here since this is a pure-Python, no-compiled-artifacts package,
  but namcap would still be the authoritative check).
- Anything involving the real tarball URL, since no GitHub remote exists
  for this repo yet.

**Recommendation for closing this gap for real:** an `archlinux:base-devel`
Docker/Podman container (or a GitHub Actions job using
`archlinux/archlinux:base-devel` as the container image) is the standard,
realistic way to run `makepkg --printsrcinfo`, `makepkg -si`, and `namcap`
in CI once this repo has a real GitHub remote and at least one tagged
release to point the PKGBUILD's `source=` at.

---

## Debian/apt (.deb)

**Status: full `debian/` control tree written and validated as far as
possible without a real Debian/Ubuntu environment. No `.deb` has actually
been built from it yet.**

### What exists

`packaging/debian/` — note the path: real `dpkg-buildpackage` expects a
directory literally named `debian/` at the repository root, not nested
under `packaging/`. Consistent with every other surface in this repo living
under `packaging/<surface>/` rather than cluttering the repo root, this
tree is kept at `packaging/debian/` and must be copied or symlinked into
place as `./debian/` immediately before building
(`cp -r packaging/debian debian` from the repo root, or a build step doing
the equivalent in CI — see SPEC.md section 31.7's release workflow, which
already needs to produce a `.deb` release asset). This is a deliberate
repo-layout choice, not an oversight — document it prominently wherever the
release workflow is implemented so `dpkg-buildpackage` is never run
directly against `packaging/debian/` in place.

Files:

- **`control`** — `Source: chwrite`, `Build-Depends: debhelper-compat (=
  13)` (the modern replacement for a `debian/compat` file — see below for
  why that file is intentionally absent), `Package: chwrite`,
  `Architecture: all` (pure Python, no compiled artifacts),
  `Depends: ${misc:Depends}, python3`, and a policy-compliant synopsis +
  extended description.
- **`rules`** — the modern minimal `dh $@` pattern. Every `dh_auto_*` step
  (`configure`/`build`/`test`/`install`) is explicitly overridden to a
  no-op or a smoke test, specifically so debhelper's Python build-system
  autodetection never fires and tries to run `pip install .` against this
  repo's `pyproject.toml` (that is the separate, independent PyPI packaging
  surface — see the "pip / pipx (PyPI)" section of this README once that
  agent's work lands; the two packaging surfaces must not become coupled).
  `override_dh_auto_test` runs `python3 chwrite.py --help` as a build-time
  smoke test, same rationale as the AUR `check()` function above. Actual
  file placement is entirely declarative via `debian/install`.
- **`install`** — maps `chwrite.py` and the existing, unmodified `chwrite`
  POSIX launcher shim both into `usr/bin/`. **This deliberately does not
  follow a `/usr/lib/chwrite/` + thin `/usr/bin/chwrite` wrapper split**
  (a reasonable alternative some Debian packages use for private
  implementation scripts) because the existing `chwrite` shim's own path
  resolution logic (`./chwrite`, unchanged/untouched — section 19/26 scope)
  looks for `chwrite.py` either in `~/.config/chwrite/` or *right next to
  itself*. Installing both files into the same directory is what makes the
  existing, unmodified shim work correctly with zero new wrapper code —
  the same layout choice `packaging/aur/PKGBUILD` makes, for the same
  reason, kept consistent across both surfaces.
- **`changelog`** — starts at `chwrite (1.0.0-1) unstable; urgency=medium`,
  hand-written in exactly the format `dch` would produce for a first entry
  (verified against the real Debian changelog grammar: `Source (Version)
  Distribution; urgency=X`, a bullet body, and a ` -- Maintainer <email>
  RFC5322-date` trailer line — see "Local validation performed" below for
  how this was checked without `dpkg-parsechangelog` available).
- **`copyright`** — DEP-5 machine-readable format
  (`https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/`),
  transcribing the repo's existing MIT `LICENSE` file verbatim. This file
  is not explicitly listed in SPEC.md section 31.5's file list but is a
  hard requirement for a policy-compliant Debian package (`lintian` treats
  a missing `debian/copyright` as a serious/fatal issue) — added here as a
  necessary addition, not scope creep.
- **`docs`** — installs `README.md` and `SPEC.md` into
  `/usr/share/doc/chwrite/` via `dh_installdocs`.
- **`source/format`** — `3.0 (native)`. chwrite has no separate
  upstream-vs-Debian-packaging split (there is no "upstream tarball" this
  packaging sits on top of independent of this repo, unlike e.g. a library
  packaged by a distro that didn't write the library) — the repo itself
  *is* both upstream and the packaging source, which is exactly the case
  `3.0 (native)` is for. `3.0 (quilt)`, by contrast, is for packaging a
  separate upstream tarball plus a patch series on top of it, which does
  not apply here.

**Known defect to fix before building:** `debian/rules` must be mode `755`
(executable) — `dpkg-buildpackage` fails immediately otherwise. It is
currently `644` in this deliverable because the sandboxed tool used to
author these files could not `chmod` a file it doesn't own at the OS level
(see the accompanying report for why). Whoever lands this must run
`chmod 755 packaging/debian/rules` (and re-`git add` it) as part of
integrating this work — a one-line fix, called out here so it isn't missed.

### Two placeholders, same shape as every other surface

1. `davidawad/chwrite` in `control`'s `Homepage:` and
   `copyright`'s `Source:`.
2. Nothing else — unlike Homebrew/AUR, this `.deb` is not built by
   downloading a release tarball at build time; `dpkg-buildpackage` builds
   directly from a checked-out source tree (that's what `3.0 (native)`
   means), so there is no `sha256`/checksum placeholder to fill in here.
   The checksum step happens later, at release-asset-attachment time (see
   SPEC.md section 31.7 — `checksums.txt` covers every release artifact,
   including the built `.deb`).

### No hosted apt repo needed for this to be useful — read this before assuming more setup is required

Per SPEC.md section 31.5 explicitly: **CI attaching the built `.deb` as a
GitHub Release asset on every tag is the realistic near-term outcome.**
`dpkg -i chwrite_1.0.0-1_all.deb` (downloaded from a GitHub Release) works
with zero hosted-repo infrastructure. A real `apt install chwrite` — via
either a Launchpad PPA or a self-hosted apt repo (`reprepro`/`aptly` serving
static files, e.g. from GitHub Pages) — is optional, later, separate work,
not a blocker for this packaging surface to be genuinely useful. If/when it
is pursued: a Launchpad PPA requires an Ubuntu One account + GPG key
registered with Launchpad (human, one-time) and `dput`-ing signed source
packages there per release; a self-hosted apt repo requires no external
account at all but does require someone to run `reprepro`/`aptly` and host
the resulting `Packages`/`Release` files somewhere with a stable URL
(GitHub Pages is the natural zero-infra choice, mirroring how the AUR/npm/
pip surfaces above lean on GitHub's own hosting wherever possible).

### Local validation performed (this pass, no Debian/Ubuntu environment)

Environment: macOS-based sandbox. None of `dpkg-buildpackage`, `debuild`,
`lintian`, `dpkg-parsechangelog`, or `dh` are available
(`command -v <tool>` resolves to nothing for all five). `brew info dpkg`
was checked as a possible source of at least `dpkg-parsechangelog` —
Homebrew's `dpkg` formula exists but pulls from `formulae.brew.sh`'s API,
which returned `HTTP 403` in this sandbox's network policy (same
sandboxed-network restriction the Homebrew agent's `packaging/README.md`
section above documents hitting against `rubygems.org`/`ghcr.io`) —
installing it was not possible here regardless. Even if it had installed,
Homebrew's `dpkg` formula is explicitly documented as "not configured to
install software" (its own caveat text) and does not include
`debhelper`/`dh`, so it would only ever have validated a subset
(`dpkg-parsechangelog`) even in the best case.

What *was* verified without any Debian-specific tooling:

- **`control`**: a Python script confirmed the file has exactly two RFC822
  stanzas (`Source:` / `Package:`) and that every field this package
  actually needs (`Maintainer`, `Build-Depends`, `Architecture`, `Depends`,
  `Description`) is present in the right stanza.
- **`changelog`**: a Python script parsed the header line against the real
  Debian changelog grammar
  (`^(\S+) \(([^)]+)\) (\S+); urgency=(\S+)$`) and confirmed
  `pkg=chwrite version=1.0.0-1 dist=unstable urgency=medium`; separately
  confirmed exactly one ` -- Name <email>  date` trailer line exists and
  that its date parses as a valid RFC 5322 timestamp via Python's
  `email.utils.parsedate_to_datetime` (the same date grammar
  `dpkg-parsechangelog` itself requires).
- **`source/format`**: confirmed the file's literal contents are exactly
  `3.0 (native)\n`, the only string dpkg's source-format parser accepts for
  that mode.
- **`rules`**'s actual smoke-test command
  (`python3 chwrite.py --help`) was run for real against this repo's
  `chwrite.py` and confirmed to exit 0 — same command, same result as the
  AUR `check()` validation above (both surfaces intentionally run the
  identical smoke test).
- File presence and the `debian/install` source→destination mapping were
  checked by hand against what `chwrite.py`/`chwrite` actually are in this
  repo (both exist at repo root, both already mode `755` at the source,
  which `dh_install` preserves).

What was **not** and **cannot** be verified without a real Debian/Ubuntu
environment:

- Whether `dpkg-buildpackage`/`debhelper` actually accepts this `rules`
  file's override sequence (debhelper's own internal sequencing logic,
  compat-level-specific behavior for `debhelper-compat (= 13)`, etc.).
- Whether the resulting `.deb` actually installs cleanly with `dpkg -i` /
  `apt install ./chwrite*.deb` and that `chwrite --help` works post-install
  from a clean chroot.
- `lintian` policy compliance beyond the manual checks above (there are
  dozens of lintian tags this wasn't able to check for: `Standards-Version`
  currency, `debian/copyright` DEP-5 strictness beyond "well-formed",
  `Vcs-*` field presence conventions, etc.).

**Recommendation for closing this gap for real, matching SPEC.md section
30's existing CI philosophy:** exactly as SPEC.md section 31.5 anticipates,
an `ubuntu-latest` GitHub Actions job with `apt-get install -y dpkg-dev
debhelper lintian` is realistic and should be added to the release workflow
(`.github/workflows/release.yml`, SPEC.md section 31.7) to actually build
and lint the `.deb` before it's attached to a release — this environment
cannot do that, but a standard GitHub-hosted Ubuntu runner can, with zero
new infrastructure beyond what section 30's `acceptance` job already uses.
