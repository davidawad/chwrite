# chwrite — Cross-Platform Repository File Protection

## 1. Purpose

chwrite is a dependency-free command-line tool that allows a Git repository to declare files or directories that should not be modified by coding agents, scripts, editors, or other processes.

A repository commits a small policy file:

```text
.write_protect
```

Example:

```text
version 1

protect package-lock.json
protect pnpm-lock.yaml
protect :(glob)src/generated/**
protect :(glob)migrations/**
protect .github/workflows/release.yml
```

chwrite applies the strongest file protection available on the local operating system.

The core implementation is a single Python 3 file using only the Python standard library and OS-provided utilities.

No pip packages, Node packages, daemons, databases, or external libraries.

Supported platforms:

* macOS
* Linux
* Windows
* BSD/POSIX systems via fallback behavior
* arbitrary Git repositories

Git is the only required external program besides Python.

---

## 2. Goals

chwrite MUST:

1. Allow protection rules to live inside the repository.
2. Protect arbitrary tracked files or directories.
3. Automatically recognize protection rules after cloning a repository when chwrite is installed globally.
4. Work without root/admin privileges where the OS permits it.
5. Use stronger privileged protection optionally where available.
6. Restore the original permissions/flags when files are unlocked.
7. Never silently modify repository contents.
8. Never require an agent to voluntarily respect an instruction file.
9. Provide a portable fallback when the OS lacks a true immutable-file mechanism.
10. Clearly report the actual strength of protection being provided.

---

## 3. Non-goal / Security Boundary

chwrite MUST NOT claim that an unprivileged package can make files absolutely immutable against a malicious process running as the same privileged OS identity.

That is impossible to guarantee portably.

For example:

* macOS `uchg` prevents modification but can be removed by the file owner.
* Linux's true `immutable` attribute prevents modification, deletion, renaming, and write-open operations, but setting or clearing it requires root or `CAP_LINUX_IMMUTABLE`.
* Windows ACLs can explicitly deny writes, but the owner of an object implicitly has authority to modify its DACL.

chwrite therefore defines explicit enforcement levels.

---

## 4. Enforcement Levels

Every protected file MUST report one of these states:

```text
HARD
ENFORCED
READONLY
VERIFY
UNPROTECTED
```

### HARD

The current process/user cannot remove the protection without privilege escalation.

Linux:

```bash
sudo chattr +i file
```

Linux immutable files cannot be modified, renamed, deleted, linked, or opened for writing; changing the immutable flag requires root or `CAP_LINUX_IMMUTABLE`.

This mode may require elevated privileges.

chwrite MUST NEVER invoke `sudo` automatically.

The user must explicitly request:

```bash
chwrite lock --hard
```

### ENFORCED

The OS actively rejects normal write attempts, but the same user can deliberately remove the protection.

macOS:

```bash
chflags uchg file
```

Windows:

```text
NTFS ACL write-denial
```

No admin privileges should normally be required when the current user owns the files.

On macOS, `UF_IMMUTABLE` overrides normal Unix permissions and prevents normal modification/move/delete operations, although the owner can remove the flag.

### READONLY

chwrite removes ordinary write permissions.

POSIX:

```bash
chmod a-w file
```

Windows fallback:

```text
FILE_ATTRIBUTE_READONLY
```

This prevents ordinary accidental writes but should NOT be described as strong protection. A process running as the owner can generally restore write permissions.

### VERIFY

chwrite cannot prevent modification but detects it before Git operations such as commit or push.

This is the minimum universal fallback.

---

## 5. Repository Policy File

Filename:

```text
.write_protect
```

Every filename variant (this section and 24) derives from a single constant, `POLICY_BASENAME` in `src/chwrite/policy.py` — renaming the policy file's basename is a one-line source change, not a repo-wide find/replace.

The format is deliberately simple and parseable without external libraries.

Syntax:

```text
version 1

# Comments start with #

protect package-lock.json
protect :(glob)generated/**
protect :(glob)database/migrations/**
protect .github/workflows/deploy.yml
```

Patterns use Git pathspec syntax rather than inventing another glob language.

Git already provides pathspec matching, including `:(glob)` and `**`.

chwrite resolves policies with:

```bash
git ls-files -z -- <pathspec>
```

This gives chwrite the same repository-relative path semantics on every OS.

Rules apply only to files contained inside the repository root.

Path traversal outside the repository MUST be rejected.

Symlinks MUST NOT be followed outside the repository.

---

## 6. CLI

Primary executable:

```bash
chwrite
```

Required commands:

```text
chwrite install
chwrite uninstall

chwrite apply
chwrite lock
chwrite unlock
chwrite status
chwrite verify

chwrite lock --hard
chwrite doctor
```

### `chwrite install`

Performs one-time installation for the current OS user.

It:

1. Installs chwrite under the user's home/config directory.
2. Installs a global Git hook dispatcher.
3. Configures Git to invoke the dispatcher.
4. Requires no root/admin privileges.
5. Detects an existing `core.hooksPath` and refuses to overwrite it without explicit permission.

Git supports centrally configured hooks through `core.hooksPath`.

Example:

```bash
git config --global core.hooksPath ~/.config/chwrite/hooks
```

Windows uses the appropriate user config directory (`%APPDATA%\chwrite`).

---

## 7. Automatic Post-Clone Behavior

chwrite installs a global:

```text
post-checkout
```

Git hook.

The hook executes:

```text
chwrite apply --quiet
```

Git explicitly runs `post-checkout` after a normal `git clone`, unless clone was performed with `--no-checkout`.

Therefore:

```bash
git clone example/repository
```

can result in:

```text
clone
  ↓
checkout
  ↓
global post-checkout hook
  ↓
chwrite sees .write_protect
  ↓
chwrite protects declared files
```

No executable code from the repository itself needs to be trusted or executed. The repository contains only the declarative `.write_protect` policy.

---

## 8. Git Hooks

The global chwrite hook dispatcher supports:

```text
post-checkout
post-merge
post-rewrite
pre-commit
pre-push
```

### post-checkout

Run:

```bash
chwrite apply
```

### post-merge

Reapply protection to files introduced or replaced by merge/pull.

### post-rewrite

Reapply protection after operations that rewrite commits.

### pre-commit

Run:

```bash
chwrite verify
```

Reject a commit if protected files have unauthorized working-tree/index modifications.

### pre-push

Optionally verify that commits being pushed do not contain prohibited modifications according to configured policy.

Git hooks can abort operations such as commits and pushes by returning nonzero status.

---

## 9. State Storage

Runtime state MUST NOT be committed.

Use:

```text
.git/chwrite/
```

Example:

```text
.git/chwrite/state.json
```

State contains:

```json
{
  "version": 1,
  "files": {
    "package-lock.json": {
      "backend": "macos-uchg",
      "original_mode": 420,
      "locked": true
    }
  }
}
```

State records enough information to restore the original file state.

chwrite MUST NOT depend solely on this state to determine whether a file is currently protected. `status` MUST inspect the actual OS state.

---

## 10. macOS Backend

Preferred backend:

```bash
chflags uchg <file>
```

Unlock:

```bash
chflags nouchg <file>
```

Classification:

```text
ENFORCED
```

No `sudo` required for files owned by the current user.

Optional privileged mode (`sudo chflags schg <file>`) is NOT automatically implemented initially because it substantially complicates normal Git operations.

macOS documents `uchg` as user immutable and `schg` as system immutable.

Fallback:

```bash
chmod a-w <file>
```

---

## 11. Linux Backend

chwrite detects whether `chattr` exists and whether the filesystem supports immutable attributes.

Default unprivileged mode:

```bash
chmod a-w <file>
```

Classification:

```text
READONLY
```

Optional:

```bash
chwrite lock --hard
```

uses:

```bash
chattr +i <file>
```

Classification:

```text
HARD
```

If privilege is required, chwrite prints the command that must be run rather than silently invoking privilege escalation.

Example:

```text
Hard protection requires CAP_LINUX_IMMUTABLE.

Run:

    sudo chwrite lock --hard
```

Linux documents the immutable attribute as preventing writes, deletion, renaming, and metadata changes, and requires root or `CAP_LINUX_IMMUTABLE` to set or clear it.

---

## 12. Windows Backend

chwrite uses native Windows ACLs.

Implementation calls the built-in:

```text
icacls.exe
```

No third-party Python package is required.

chwrite:

1. determines the current user's SID;
2. saves the current ACL;
3. adds an explicit deny for modification rights;
4. restores the previous ACL during unlock.

Microsoft documents explicit deny ACE support through `icacls`.

Two Windows limitations are documented explicitly:

First, an object owner can modify its DACL.

Second, Windows permits deletion/renaming when either the file grants DELETE or its parent directory grants `FILE_DELETE_CHILD`. Therefore protecting an individual file without restricting its parent cannot universally prevent replacement/deletion.

Third — confirmed on real `windows-latest` GitHub Actions CI, not just reasoned about: `icacls file /deny user:(W)` denies *reads*, not just writes. icacls's simple "W" permission alias silently bundles in DELETE, and (verified bit-by-bit against a real Windows runner) denying DELETE alone — independent of any read-related right — is sufficient to make an ordinary read-open fail with access denied. chwrite therefore denies the granular rights `(WD,AD,WEA,WA)` (write data / append data / write extended attributes / write attributes) instead of the simple `(W)` alias, omitting DELETE entirely. This doesn't weaken the second limitation above — file-level DELETE denial was already documented as unreliable given the parent-directory caveat — it just avoids a real, previously-undiscovered bug where the "safe" simple alias broke reads outright.

The normal Windows backend is therefore classified:

```text
ENFORCED
```

rather than `HARD`.

---

## 13. Generic POSIX Backend

For an unknown Unix-like OS:

```python
os.chmod(...)
```

removes all write bits while preserving read/execute bits.

Classification:

```text
READONLY
```

Unlock restores the original mode stored under `.git/chwrite/state.json`.

---

## 14. Unknown Operating Systems

chwrite remains usable rather than fail outright.

Fallback behavior:

```text
1. Resolve protected files.
2. Record baseline Git object hashes.
3. Install verification hooks.
4. Report protection level VERIFY.
```

Example:

```text
$ chwrite status

PROTECTION  FILE
READONLY    package-lock.json
VERIFY      special/generated.bin
```

---

## 15. Status Command

Example:

```text
$ chwrite status

chwrite v1

Policy: /project/.write_protect

LEVEL      BACKEND         FILE
ENFORCED   macos-uchg      package-lock.json
ENFORCED   macos-uchg      migrations/001.sql
ENFORCED   macos-uchg      migrations/002.sql

3 protected
0 violations
```

Linux:

```text
LEVEL      BACKEND
HARD       linux-immutable
READONLY   posix-mode
```

Windows:

```text
LEVEL      BACKEND
ENFORCED   windows-acl
```

---

## 16. Unlock Workflow

Individual:

```bash
chwrite unlock package-lock.json
```

Everything:

```bash
chwrite unlock --all
```

Temporary command:

```bash
chwrite unlocked -- git pull
```

Optional later feature:

```bash
chwrite unlocked -- npm install
```

Semantics:

1. unlock protected files;
2. execute command;
3. reapply protections even if the command fails;
4. propagate the child command's exit status.

This is useful because OS-level protection can intentionally prevent Git itself from replacing protected files during checkout, merge, or pull.

---

## 17. Verification

`chwrite verify` MUST detect:

* modified protected files;
* deleted protected files;
* replaced protected files;
* protected symlink target changes;
* staged modifications;
* protection flags that have been removed.

Suggested output:

```text
ERROR: chwrite violation

M package-lock.json
D migrations/001.sql

Run:

    chwrite status
```

Exit status:

```text
0 = valid
1 = protection violation
2 = configuration/runtime error
```

---

## 18. Security Requirements

chwrite MUST:

* never execute content from `.write_protect`;
* treat `.write_protect` purely as data;
* reject absolute paths;
* reject paths resolving outside repository root;
* avoid following symlinks outside repository root;
* use argument arrays rather than shell interpolation;
* correctly handle filenames containing spaces;
* use NUL-delimited Git output where possible;
* never automatically invoke `sudo`, `su`, UAC, or another privilege escalation mechanism;
* never overwrite an existing Git hook configuration silently;
* restore original permissions during uninstall/unlock;
* behave idempotently.

Running:

```bash
chwrite apply
```

ten times MUST produce the same result as running it once.

---

## 19. Distribution

Preferred distribution:

```text
chwrite.py
```

Single-file Python implementation.

Optional launchers:

```text
chwrite       # POSIX shell shim
chwrite.cmd   # Windows launcher
```

No Python packages. No installer framework required.

Possible installation:

```bash
python chwrite.py install
```

After installation:

```bash
chwrite status
```

should work globally.

---

## 20. Repository Experience

Repository maintainer:

```bash
chwrite init
```

creates:

```text
.write_protect
```

Then:

```bash
chwrite add package-lock.json
chwrite add ':(glob)migrations/**'
```

Result:

```text
version 1

protect package-lock.json
protect :(glob)migrations/**
```

Commit:

```bash
git add .write_protect
git commit -m "Protect generated files"
```

Anyone with chwrite globally installed gets the policy automatically after cloning because Git invokes the global `post-checkout` hook following clone.

Someone without chwrite can still clone and use the repository normally; `.write_protect` is inert data.

---

## 21. Required Initial Commands

Version 1 implements only:

```text
chwrite init
chwrite add <pathspec>
chwrite remove <pathspec>

chwrite apply
chwrite lock
chwrite unlock
chwrite status
chwrite verify

chwrite install
chwrite uninstall
chwrite doctor
```

Avoid unnecessary package complexity.

---

## 22. Guarantee Model

```text
HARD
    Requires privilege to defeat.

ENFORCED
    OS rejects writes, but same user can deliberately disable protection.

READONLY
    Ordinary write permission removed.

VERIFY
    Modification can occur, but chwrite detects/rejects it later.
```

This is preferable to falsely claiming identical security semantics across macOS, Linux, Windows, and arbitrary filesystems.

The principal use case is preventing coding agents from accidentally or autonomously modifying repository files they have no reason to touch, not defending the machine against a malicious administrator.

---

## 23. Acceptance Test

Given:

```text
.write_protect

version 1
protect protected.txt
```

After:

```bash
chwrite apply
```

this command:

```python
open("protected.txt", "w").write("changed")
```

SHOULD fail whenever the current OS backend provides `ENFORCED` or `HARD` protection.

This:

```bash
chwrite verify
```

MUST fail if the protected file has changed under every supported platform.

This:

```bash
chwrite unlock protected.txt
```

MUST restore normal write behavior.

This:

```bash
chwrite apply
```

MUST restore protection.

The same repository and `.write_protect` file MUST function unchanged on macOS, Linux, and Windows.

## Sources

Git hooks and `post-checkout`/clone behavior: Git official documentation.
Git centralized `core.hooksPath`: Git official documentation.
Git pathspec syntax: Git official documentation.
macOS immutable file flags: Apple File System Programming Guide.
Linux immutable file attribute: Linux `chattr(1)` documentation.
Windows ACL enforcement: Microsoft `icacls` documentation.
Windows ownership/DACL semantics: Microsoft Win32 security documentation.
Windows delete/rename semantics: Microsoft Win32 `DeleteFile` documentation.

---

## 24. Per-Rule Messages and Alternate Policy Formats

Every `protect` rule MAY carry an optional human-readable `message`, shown to whoever (or whatever) is blocked from writing the file — e.g. `"Managed by CI, do not hand-edit"` or `"Dan doesn't want this file touched right now"`.

Exactly one policy file may exist at the repository root, auto-detected by filename. chwrite errors (exit 2) if more than one is present, since that is ambiguous.

Supported filenames, in order chwrite checks for them:

```text
.write_protect         plain pathspec format (section 5), extended below
.write_protect.json
.write_protect.toml
.write_protect.yaml / .write_protect.yml
```

All four formats express the same schema and are interchangeable — pick whichever a given repo's maintainers prefer. This is a convenience for humans authoring policy; it has no effect on enforcement, which is identical regardless of source format.

### 24.1 `.write_protect` (plain) — extended syntax

```text
version 1

protect package-lock.json message="Managed by CI, do not hand-edit"
protect :(glob)migrations/** message="Migrations are append-only"
protect .github/workflows/release.yml
```

The `message="..."` suffix is optional per line. Double quotes only; no escaping beyond a literal `\"` for a literal quote. A line without a message uses the default message (section 24.3).

### 24.2 `.write_protect.json` / `.write_protect.toml` / `.write_protect.yaml`

Common shape:

```json
{
  "version": 1,
  "protect": [
    {"pattern": "package-lock.json", "message": "Managed by CI, do not hand-edit"},
    {"pattern": ":(glob)migrations/**", "message": "Migrations are append-only"},
    {"pattern": ".github/workflows/release.yml"}
  ]
}
```

TOML:

```toml
version = 1

[[protect]]
pattern = "package-lock.json"
message = "Managed by CI, do not hand-edit"

[[protect]]
pattern = ".github/workflows/release.yml"
```

YAML:

```yaml
version: 1
protect:
  - pattern: package-lock.json
    message: "Managed by CI, do not hand-edit"
  - pattern: .github/workflows/release.yml
```

Parsing constraints, to honor the "no external libraries" rule in section 1:

* JSON: stdlib `json`.
* TOML: stdlib `tomllib` (read-only; Python 3.11+). If running under an older interpreter, TOML support fails with a clear "requires Python 3.11+" error rather than guessing.
* YAML: chwrite ships a small internal parser covering only the subset needed for this exact schema (a top-level mapping of scalar `version` and a `protect` sequence of mappings with string `pattern`/`message` keys, `#` comments, 2-space indentation). It is not a general YAML parser and MUST reject anything outside that subset (anchors, multi-doc streams, flow collections, non-string scalars for `pattern`/`message`) with a clear error rather than silently misinterpreting it. This is a deliberate, documented limitation, not a bug.

### 24.3 Default message

A rule with no explicit message uses: `"protected by chwrite policy — see .write_protect"` (or the actual policy filename in use).

### 24.4 Ad hoc local locks

Independent of the committed policy, a user can protect a path on their own machine only, with its own message, without editing the policy file:

```bash
chwrite lock <path> --message "Dan doesn't want this file touched right now"
```

This is recorded only in the uncommitted local state (`.git/chwrite/state.json`, section 9) — never in the repo's `.write_protect*` policy file. It behaves exactly like a policy-driven protection (same enforcement-level rules, same backends) but is personal and machine-local, e.g. "I'm mid-refactor on this file, don't let anything touch it until I say so." `chwrite unlock <path>` removes it.

When both a policy rule and an ad hoc lock apply to the same file, the ad hoc lock's message takes precedence when reporting to the user/agent, since it is the more specific, more recently expressed intent.

---

## 25. Agent-Facing Enforcement Hook

OS-level protection (chflags/chattr/icacls) has no facility to display a custom message on write failure — the calling process just sees `EACCES`/`EPERM`/Win32 `ERROR_ACCESS_DENIED`. To surface the human-authored message *before* an agent even attempts the write, chwrite provides a fast, scriptable check:

```bash
chwrite check-path <path>
```

Behavior:

* Exit 0, no output, if `<path>` is not protected.
* Exit 1 and print the rule's message (policy message, or ad hoc lock message if present, else the default from 24.3) to stderr, if `<path>` is protected.
* Exit 2 on a configuration/runtime error (mirrors `verify`'s exit codes, section 17).

This is intentionally cheap (no subprocess spawn beyond what's needed to resolve the path against policy + local state) so it is safe to call on every file-editing tool invocation.

### 25.1 Claude Code integration

```bash
chwrite install --claude-hook
```

adds a project-scoped `PreToolUse` hook (matcher `Edit|MultiEdit|Write|NotebookEdit`) to this repository's `.claude/settings.json` that runs `chwrite check-path` against the tool call's target file and blocks the tool call with the rule's message when it fails. This is committed with the repo (unlike the global git-hook install in section 6, which is a one-time per-machine setup) so that anyone using Claude Code against this repository gets the same protection at the tool layer, in addition to the OS layer. It is additive, not a replacement: OS-level enforcement (sections 10-14) is what actually stops the write; this hook exists purely to give a fast, legible explanation instead of a raw OS permission error, and to stop the write before it's attempted at all where the OS backend is only VERIFY.

`chwrite install --claude-hook` must not silently clobber an existing `PreToolUse` hook block in `.claude/settings.json` — same non-destructive rule as section 6's `core.hooksPath` handling. If hooks already exist there, it appends its own hook entry rather than overwriting the file's `hooks` key, and refuses with a clear message if it cannot do so safely (e.g. malformed existing JSON).

---

## 26. Source Layout vs. Distributed Artifact

Section 19's "single Python 3 file" requirement describes the **distributed artifact** an end user drops into their toolchain — it does not mean the maintained source tree is one undifferentiated file. This repo follows the standard project layout (`config/programming-languages/python/README.md`): `uv` for the dev/test environment, `ruff` (format + lint), `pyright --strict`, `pytest`, a `justfile`, and the swe Python plugin pack's gates — including its 600-line-per-file ceiling — apply to the **source modules**, not to the generated bundle.

```text
chwrite/
├── justfile
├── pyproject.toml          # ruff + pyright + pytest config (dev-only deps)
├── uv.lock
├── .python-version
├── src/chwrite/
│   ├── __init__.py
│   ├── __main__.py         # argparse entrypoint / command dispatch
│   ├── policy.py           # .write_protect / .write_protect.json / .toml / .yaml parsing (section 5, 24)
│   ├── policy_yaml.py       # the documented YAML subset parser (section 24.2)
│   ├── state.py             # .git/chwrite/state.json read/write (section 9)
│   ├── gitutil.py           # git ls-files / pathspec resolution, repo-root safety (section 5, 18)
│   ├── backends/
│   │   ├── __init__.py      # backend selection by platform.system()/sys.platform
│   │   ├── macos.py         # chflags uchg/nouchg (section 10)
│   │   ├── linux.py         # chmod / chattr (section 11)
│   │   ├── windows.py       # icacls (section 12)
│   │   ├── posix_generic.py # chmod a-w fallback (section 13)
│   │   └── unknown.py       # VERIFY-only fallback (section 14)
│   ├── hooks.py             # git hook dispatcher body + install/uninstall (sections 6-8)
│   ├── claude_hook.py       # --claude-hook / check-path integration (section 25)
│   └── cli.py               # subcommands: init/add/remove/apply/lock/unlock/status/verify/
│                             # check-path/doctor/install/uninstall (section 21, 24, 25)
├── scripts/
│   └── bundle.py            # concatenates src/chwrite/* into the single-file artifact below
├── chwrite.py                # GENERATED — the actual distributable. Do not hand-edit;
│                             # regenerate with `just build` / `python3 scripts/bundle.py`.
├── chwrite                   # POSIX launcher shim (unchanged, section 19)
├── chwrite.cmd                # Windows launcher (unchanged, section 19)
└── tests/                    # pytest, mirrors src/chwrite/ (section 27)
```

### 26.1 The bundler

`scripts/bundle.py` is itself a small stdlib-only script. It topologically orders `src/chwrite`'s modules (or simply concatenates them in a fixed, hand-declared order — no import cycles are allowed, so this is trivial), strips intra-package `from chwrite... import ...` / `from . import ...` lines, and writes a single `chwrite.py` at repo root with:

* one shebang (`#!/usr/bin/env python3`),
* a generated-file header comment naming the source commit/script, so nobody mistakes it for hand-maintained source,
* stdlib imports de-duplicated and hoisted to the top,
* the original module contents in dependency order,
* a `if __name__ == "__main__":` entrypoint calling the CLI dispatch.

`just build` runs the bundler and is a required step before `just ci`'s final gate (the bundle is verified importable/executable, and `chwrite.py --help` is smoke-tested, as part of `just test`). The generated `chwrite.py` IS committed to the repo (distribution needs a stable raw-file URL / path to `curl`), so `just build` must be re-run and the diff committed whenever `src/chwrite/` changes — `just ci` fails if the committed `chwrite.py` is stale relative to `src/chwrite/` (regenerate to a temp path and diff).

### 26.2 What this changes about section 1 / 19

Nothing about behavior, dependencies, or the guarantee model changes. "No pip packages, no external libraries" still governs `chwrite.py` (the artifact) at runtime for end users. `uv`/`ruff`/`pyright`/`pytest`/hypothesis-if-used are dev-only tooling for this repo's own maintainers and never become a runtime dependency of the shipped file.

---

## 27. Testing & Coverage

Per the standalone-project floor in `config/programming-languages/python/README.md` section 8: `pytest tests/ --cov=src/chwrite --cov-fail-under=85`. Tests mirror `src/chwrite/` structure under `tests/` (never colocated). Cover, at minimum:

* policy parsing for all four formats (section 5, 24), including the documented YAML-subset rejection cases;
* pathspec-to-file resolution and the path-traversal/symlink-escape rejections (section 18);
* state read/write round-trip (section 9);
* the macOS backend end-to-end on this dev machine (apply/verify/unlock/re-apply, section 23's acceptance test literally, run against the real filesystem in a throwaway git repo — not mocked, since the whole point is real OS enforcement);
* Linux/Windows backends at minimum unit-tested against mocked `subprocess.run` calls (can't exercise real chattr/icacls on a macOS CI-less dev box) — command construction, argument arrays, exit-code handling, and the privilege-failure message path (section 11's "print the sudo command, never invoke it") are all testable without the real OS primitive;
* `verify`/`check-path` exit codes (0/1/2) under valid, violated, and config-error conditions;
* idempotency: `apply` run ten times matches `apply` run once (section 18).

---

## 28. Regex-Based Matching

Alongside pathspec rules (`protect <pathspec>`), policy files may declare regex rules:

```text
protect-regex ^migrations/.*\.sql$ message="Migrations are append-only"
```

Structured formats (`.write_protect.json`/`.toml`/`.yaml`) express this as an entry with a `regex` field instead of `pattern`:

```json
{"regex": "^migrations/.*\\.sql$", "message": "Migrations are append-only"}
```

Exactly one of `pattern` or `regex` is set per entry — never both. Same for the plain format: a line is either `protect <pathspec> ...` or `protect-regex <pattern> ...`, never a hybrid.

**Resolution model — regex never resolves paths itself.** chwrite always starts from `git ls-files -z` (the same repo-relative, safety-checked file list pathspec rules use — see section 5/18). A regex rule is applied as `re.search(pattern, relpath)` (Python stdlib `re`) against each already-tracked, already-inside-repo-root path from that list, using forward-slash-normalized paths on every OS (so a pattern written on macOS matches identically on Windows). This means the path-traversal and symlink-escape protections in section 18 apply identically to regex rules — regex is strictly a filter over a pre-validated candidate list, never a path resolver, so it cannot be used to reach outside the repo root.

An invalid regex (fails `re.compile`) is a config error: exit 2, clear message naming the bad pattern, never a silent zero-match.

**Precedence:** a file matched by more than one rule (pathspec and/or regex) is still protected exactly once; if multiple matching rules carry different messages, the last-defined matching rule's message wins (policy files are read top-to-bottom; document this explicitly in `chwrite doctor`/`status` if ambiguity is detected, so it's never silently surprising).

---

## 29. Group/User-Scoped Restrictions

Default behavior (unchanged): a `protect` rule blocks **everyone**, including the file's own owner, until explicitly unlocked — this is what sections 10-14 already describe. This section adds an **optional, narrower** mode: deny specific identities while leaving others unaffected.

Policy / CLI:

```text
protect <pathspec> deny-user=ci-bot
protect <pathspec> deny-group=contractors
```

```bash
chwrite lock <path> --deny-user ci-bot
chwrite lock <path> --deny-group contractors
```

Multiple `deny-user=`/`deny-group=` values are comma-separated. When neither is present, behavior is exactly sections 10-14 (blanket block).

### 29.1 Why this is NOT the same guarantee strength everywhere

This feature's whole premise is per-identity denial, so it lives or dies on whether the OS backend actually HAS a deny primitive, not just an allow primitive. **This differs by platform in a way that changes the enforcement level, and chwrite must never blur that.**

**Windows — real deny-ACE.** `icacls.exe` already supports an explicit deny ACE (section 12); this feature simply generalizes the target from "current user's SID" to an arbitrary named user or group's SID (`icacls <file> /deny "<name>:(W)"`). Classification: `ENFORCED` (same caveat as section 12 — the object owner can still edit the DACL).

**macOS — real deny-ACE.** APFS/HFS+ support NFSv4-style ACL entries via `chmod +a`/`chmod -a`, which (unlike the blanket `uchg` flag) support an explicit **deny** clause for a named user or group: `chmod +a "user:ci-bot deny write,delete,append,writeattr,chown" <file>` / `chmod -a "user:ci-bot deny ..." <file>` to remove. Classification: `ENFORCED` (the file owner can still edit its own ACL).

**Linux — real for `deny-user`, best-effort for `deny-group`, and only if `setfacl`/`getfacl` (the `acl` package) and a mount with the `acl` option are present.** POSIX.1e ACLs (what `setfacl` manages) have **no deny entry type** — only allow entries plus the base owner/group/other bits. The access-check algorithm still makes single-named-user denial work correctly: a non-owner user's access is resolved by the **first matching category** (named-user entry beats group entries beats "other"), so `setfacl -m u:<name>:0 <file>` reliably blocks that specific non-owner user regardless of what the group/other bits allow, *as long as that user is not the file's Unix owner* (an owner's access is never mediated by ACLs — matches the "owner retains authority" theme everywhere else in this spec). `deny-group` is weaker: POSIX ACL group entries are **additive** — if the denied group is one of several groups granting that user access (e.g. they're also in a group with independent write rights), the union still grants write. chwrite MUST document this Linux group caveat in `status`/`doctor` output whenever a `deny-group` rule is active on Linux, not just in this spec.

If `setfacl`/`getfacl` aren't available, or the target filesystem was mounted without ACL support, chwrite MUST refuse the scoped-lock request with a clear error naming the missing capability — it MUST NOT silently fall back to a blanket block (that protects the wrong set of identities and would misrepresent what was requested) and MUST NOT silently no-op (violates goal 7, "never silently modify/not-modify"). `chwrite doctor` reports ACL-support availability the same way it reports `chattr`/`CAP_LINUX_IMMUTABLE` availability today.

**Never attempted:** chwrite MUST NOT create, modify, or otherwise administer system users or groups. `deny-group=X` requires that group already exist; if it doesn't, that's a config/runtime error (exit 2), not something chwrite fixes on your behalf.

### 29.2 State and status

`.git/chwrite/state.json` records the scope (`"scope": {"deny_user": [...], "deny_group": [...]}` or `"scope": "all"` for the default blanket mode) per file, same as it already records backend/mode. `status` inspects real ACL/ACE state (via `getfacl`/`icacls /T /Q` equivalent query/`ls -le` ACL read on macOS) rather than trusting the JSON, consistent with section 9.

---

## 30. Continuous Integration

`.github/workflows/ci.yml`, two jobs:

**`lint-and-unit`** (`ubuntu-latest` only) — the source-level dev-tooling gate: `uv sync`, `just fmt-check lint typecheck test` (ruff format/check, pyright --strict, pytest with the 85% coverage floor from section 27). Fails the build on any of these.

**`acceptance`** — matrix over `[macos-latest, ubuntu-latest, windows-latest]`. Steps: checkout, set up Python 3.11+, run `scripts/acceptance_test.py` (stdlib-only, cross-platform, no `uv`/dev deps required — it's meant to double as documentation-by-execution of section 23's acceptance test) against the committed `chwrite.py`. This script is the executable form of section 23: init a throwaway repo (under the runner's own temp dir, e.g. `tempfile.mkdtemp()` — never inside the checked-out working tree), write a `.write_protect`, apply, assert the write fails while protected, assert `verify` catches an out-of-band flag removal, assert `unlock`/re-`apply` round-trip, assert idempotency (apply x10). It should skip (not fail) any platform-specific assertion that isn't applicable everywhere (e.g. a `deny-group` ACL-caveat check only meaningful on Linux) rather than hard-coding OS branches inline in the CI YAML.

Both jobs trigger on `push` and `pull_request`. No Docker-based filesystem matrix (ext4/xfs/btrfs/etc.) for now — real OS runners via the matrix above are the bar; this can be revisited later if chwrite needs to support filesystems not backing any of the three GitHub-hosted runner images.

---

## 31. Packaging & Distribution

`chwrite.py` at repo root (section 19/26) remains the ground-truth distributable — every packaging surface below is a thin wrapper around it or the same generated artifact, never a second implementation. All of this is versioned off `pyproject.toml`'s `version` field and git tags of the form `vX.Y.Z` (section 31.7 covers the release process that keeps them in sync).

Honesty rule, consistent with the rest of this spec: a packaging job that requires a credential/account/repo chwrite's own CI doesn't control (a PyPI trusted-publisher link, an npm token, a Homebrew tap repo, an AUR SSH key, a winget-pkgs PR) MUST be wired up and ready to run, but MUST NOT be claimed as "done" until that one-time external setup is actually completed by a human with those credentials. `packaging/README.md` tracks exactly which surfaces are live vs. wired-but-pending.

### 31.1 pip / pipx (PyPI)

The most natural fit — `src/chwrite/` is already a proper package. Add a console-script entry point (`[project.scripts] chwrite = "chwrite.cli:main"`) so `pip install chwrite` / `pipx install chwrite` puts a working `chwrite` on PATH without needing the bundled single-file artifact at all (pipx/pip users get the real package, not the curl'd bundle — both are valid, equally-supported install paths). Publish via PyPI's Trusted Publisher (OIDC from GitHub Actions — no long-lived token stored in this repo) on tagged releases.

### 31.2 npm

chwrite has no JS and no runtime dependencies, so the npm package is a thin, offline postinstall-free wrapper: ships the current `chwrite.py` directly inside the npm tarball (`files` in `package.json`), with a `bin` entry that execs it via the user's `python3`. No download-at-install-time step — that would add a network dependency and supply-chain surface this project has otherwise avoided everywhere else (section 18's whole ethos). Lives under `packaging/npm/`.

### 31.3 Homebrew

A formula (`packaging/homebrew/chwrite.rb`) in a personal tap (`homebrew-chwrite`, a separate repo — Homebrew requires taps to be their own repo named `homebrew-<name>`; this is a one-time manual repo-creation step, not something CI can bootstrap on its own). The formula downloads the tagged GitHub release source tarball, verifies its sha256, and installs `chwrite.py` + the `chwrite` shim into the Cellar with a symlink into `bin`.

### 31.4 AUR (Arch Linux)

`packaging/aur/PKGBUILD` — downloads the same tagged release tarball, installs `chwrite.py`/`chwrite` into `/usr/bin`. Publishing requires push access to a personal AUR git repo tied to an AUR account's SSH key (one-time manual setup).

### 31.5 Debian/apt (.deb)

`packaging/debian/` (control, rules, changelog, compat, source/format) builds a `.deb` via `dpkg-buildpackage`. CI attaches the built `.deb` as a GitHub Release asset on every tag regardless of whether a hosted apt repo exists — `dpkg -i` from the downloaded asset always works. A real `apt install chwrite` (PPA or self-hosted apt repo) is a separate, later, optional step, not required for this to be useful.

### 31.6 winget (Windows)

`packaging/winget/` — a three-file manifest (version/installer/locale) targeting the existing `chwrite.cmd` launcher, submitted via PR to `microsoft/winget-pkgs` (not self-hostable, requires an actual PR against Microsoft's repo). This is the roughest fit of the six surfaces here — winget expects installer semantics (EXE/MSI/portable zip) more than "run this script with an interpreter" — so document the chosen manifest type's tradeoffs explicitly rather than pretending it's as clean a fit as pip/npm/brew.

### 31.7 Release process

Tagging `vX.Y.Z` (matching `pyproject.toml`'s `version`) and pushing the tag triggers `.github/workflows/release.yml`, which: builds the sdist/wheel, rebuilds and verifies the `chwrite.py` bundle is not stale (reusing section 26.1/30's staleness check), computes checksums for every artifact, creates a GitHub Release attaching `chwrite.py`/`chwrite`/`chwrite.cmd`/sdist/wheel/`.deb`/`checksums.txt`, and runs whichever publish jobs (PyPI, npm, Homebrew formula bump, AUR PKGBUILD bump) have their required secrets configured — a missing secret skips that job with a clear log line, it never fails the whole release. `packaging/README.md` is the single source of truth for which surfaces are actually live at any given time.

---

## 32. Two Binaries: `chwrite` (hot path) vs. `chwrite-setup` (one-time)

Sections 6/19/21 originally put `install`/`uninstall` on the same command surface as everything else. In practice this mixes two very different kinds of operation on one binary:

* **Hot path** (`chwrite`) — `init`, `add`, `remove`, `apply`, `lock`, `unlock`, `unlocked`, `status`, `verify`, `check-path`, `doctor`. Every one of these operates entirely within the current repo. No global state is touched.
* **One-time setup** (`chwrite-setup`) — `install`, `uninstall`. These touch `git config --global core.hooksPath` and the user's home/config directory (section 6). Run once per machine, essentially never again.

Splitting them is not a performance fix — cold start is ~40ms end to end, dominated entirely by the Python interpreter, and argparse's cost scales with subcommand *count* by microseconds regardless of which ones exist. It's a separation-of-concerns change: `chwrite`'s only job becomes "operate on this repo," full stop, and nothing in its command surface can ever mutate anything outside the repo.

### 32.1 Distribution

Both binaries are generated by `scripts/bundle.py` from disjoint subsets of `src/chwrite/`:

* `chwrite.py` (existing) — everything except `hooks.py` and the new `setup_cli.py`. `diagnostics.py`'s `doctor` needs to *report* install status without pulling in install/uninstall logic itself, so `config_dir()` moves out of `hooks.py` into a new tiny shared module, `src/chwrite/config_paths.py`, included in both bundles.
* `chwrite-setup.py` (new) — `hooks.py`, `setup_cli.py`, `config_paths.py`, plus whatever `hooks.py` itself needs (`errors.py`, `gitutil.py`, `state.py`, `backends/`, `claude_hook.py`).

Launchers mirror the existing pattern exactly: `chwrite-setup` (POSIX shell shim) and `chwrite-setup.cmd` (Windows), sitting next to `chwrite`/`chwrite.cmd` at repo root, doing nothing but locating and exec'ing the right `.py`.

### 32.2 `chwrite-setup install` no longer assumes it IS `chwrite.py`

The old, single-binary `cmd_install` copied `os.path.realpath(__file__)` into `~/.config/chwrite/chwrite.py` — that worked because the running script and the thing being installed were the same file. Once `install` lives in a separate binary, that self-reference is gone, and `install` has to find the *hot-path* `chwrite` to set up hooks against. In preference order:

1. **A `chwrite` already on PATH** (`shutil.which("chwrite")`). This is the common case for anyone who installed via pip/pipx/Homebrew/npm/AUR/apt (section 31) — those already guarantee a durable, PATH-resolvable command, so there's nothing to copy; generated hook scripts just invoke `chwrite apply --quiet` etc. directly, same as any other installed CLI tool's hooks would.
2. **A sibling `chwrite.py`** next to wherever `chwrite-setup.py` is actually running from (mirroring how the `chwrite`/`chwrite.cmd` launchers already resolve their own sibling `chwrite.py`, section 19). This is the curl-one-file case: `install` copies that file into `~/.config/chwrite/chwrite.py` (same as before) so hooks keep working even if the original downloaded copy is later deleted or moved, and generated hook scripts invoke that copy with `python3` explicitly.
3. Neither found: `chwrite-setup install` fails with a clear error rather than guessing — never silently install a broken hook.

`chwrite-setup uninstall` is unaffected by this — it never needed to know where `chwrite.py` came from, only where its own state (`config_dir()`, `core.hooksPath`) lives.

### 32.3 What doesn't change

`chwrite install --claude-hook` becomes `chwrite-setup install --claude-hook` (same flag, same behavior, section 25.1). `--force` for overwriting an existing `core.hooksPath` moves the same way. The global git hook dispatcher itself still shells out to plain `chwrite apply --quiet` / `chwrite verify` (section 8) — untouched, since hooks always call the hot-path binary, never `chwrite-setup`.
