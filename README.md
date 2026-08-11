# chwrite

Dependency-free, single-file Python CLI that lets a Git repository declare
files or directories that should not be modified — lockfiles, migrations,
generated code, CI workflow files — and enforces that with the strongest
file-protection mechanism available on the local OS.

The main use case is stopping coding agents, scripts, and editors from
touching files they have no business touching, not defending a machine
against a malicious root/admin process. See [`SPEC.md`](./SPEC.md) for the
full design and threat model (section 3, "Non-goal / Security Boundary").

## Install

See [`INSTALL.md`](./INSTALL.md) for every install method (pip/npm/Homebrew/
AUR/apt/winget) and their real current status — only git clone actually
works end-to-end today; the rest are built and tested but not yet published.

chwrite is two small binaries, not one (see SPEC.md section 32 for the full
rationale): `chwrite` is the hot path (everything that operates on the
current repo — apply/lock/unlock/status/verify/etc.); `chwrite-setup` is a
separate, one-time-per-machine binary (install/uninstall — the only two
commands that touch anything outside the current repo). `chwrite` never
imports or runs any install/uninstall code, and vice versa.

```bash
git clone <this-repo>
cd chwrite
python3 chwrite-setup.py install
```

`chwrite-setup install` installs a global Git hook dispatcher and points Git
at it via `core.hooksPath`. No root/admin privileges required. It will
refuse to overwrite an existing `core.hooksPath` without explicit
permission. If a real `chwrite` is already on your PATH (pip/pipx/Homebrew/
npm/AUR/apt install — see "Distribution" below), the generated hooks call
that directly; otherwise it copies the sibling `chwrite.py` into your user
config directory (`~/.config/chwrite` on macOS/Linux, `%APPDATA%\chwrite`
on Windows) so hooks keep working even if you later delete the original
downloaded copy.

After install, the `chwrite` / `chwrite.cmd` launchers in this repo (and
`chwrite status`, once installed globally) work from any directory.

## Quickstart

```bash
# In a repo you want to protect files in:
chwrite init                              # creates .write_protect
chwrite add package-lock.json
chwrite add ':(glob)migrations/**'
git add .write_protect
git commit -m "Protect generated files"

chwrite apply                             # lock the declared files now
chwrite status                            # see current protection state
```

Anyone who clones the repo and has chwrite installed globally gets
protection automatically: Git runs the global `post-checkout` hook after
clone, which runs `chwrite apply --quiet`. Anyone without chwrite installed
can still clone and use the repo normally — `.write_protect` is inert data, never
executed.

## `.write_protect` policy file

```text
version 1

# Comments start with #
protect package-lock.json
protect :(glob)src/generated/**
protect :(glob)migrations/**
protect .github/workflows/release.yml
```

Patterns are Git pathspecs (`git ls-files -z -- <pathspec>`), not a custom
glob language. Rules only ever apply to files inside the repo root; absolute
paths and paths that resolve outside the repo are rejected.

`.write_protect.json`, `.write_protect.toml`, and `.write_protect.yaml`/`.write_protect.yml` are
equivalent structured formats for the same schema (`pattern`/`message`
fields instead of `protect ... message="..."` lines) — pick one style per
repo. Exactly one policy file may exist at the repo root; chwrite errors if
it finds more than one. Details: [`SPEC.md`](./SPEC.md) section 24.

## Custom protection messages

Any rule can carry a message shown to whatever gets blocked from writing it:

```text
protect package-lock.json message="Managed by CI, do not hand-edit"
```

Rules with no message fall back to a generic default. Independent of the
committed policy, you can also lock a path on your own machine only, with
its own message, without touching the policy file at all:

```bash
chwrite lock path/to/file.ts --message "mid-refactor, don't touch"
```

This is personal and uncommitted — recorded only in local
`.git/chwrite/state.json`, never written to `.write_protect*`. `chwrite unlock
path/to/file.ts` removes it. Full behavior, precedence rules, and the
structured-format schema: [`SPEC.md`](./SPEC.md) sections 24-25.

## Regex rules

Alongside pathspec `protect` rules, a policy can declare regex rules that
match against the same `git ls-files`-derived path list:

```text
protect-regex ^migrations/.*\.sql$ message="Migrations are append-only"
```

Structured formats use a `regex` field instead of `pattern` (never both on
one rule). A file matched by more than one rule — pathspec or regex — is
still protected exactly once; if messages differ, the *last*-defined
matching rule wins. Details: [`SPEC.md`](./SPEC.md) section 28.

## Scoped locks (deny-user / deny-group)

By default a `protect` rule blocks everyone, including the file's owner.
To narrow that to specific identities instead:

```text
protect generated/schema.ts deny-user=ci-bot
protect vendor/** deny-group=contractors
```

```bash
chwrite lock path/to/file.ts --deny-user ci-bot
chwrite lock path/to/file.ts --deny-group contractors,interns
```

This is a **real** per-identity deny on macOS (NFSv4 ACL `chmod +a`) and
Windows (`icacls` deny ACE), and on Linux for `deny-user` (POSIX ACL named-user
entry). `deny-group` on Linux is best-effort only — POSIX ACL group entries
are additive, so it doesn't reliably block a user who has write access via
some *other* group too; `chwrite status`/`doctor` call this out whenever
it's active. Linux scoped locks require the `acl` package (`setfacl`/
`getfacl`); chwrite refuses the request outright (never silently falls back
to a blanket block) if that's unavailable. chwrite never creates or modifies
users/groups — a nonexistent name is a config error. Full platform-by-platform
guarantees: [`SPEC.md`](./SPEC.md) section 29.

## Enforcement levels

chwrite reports the actual strength of protection per file — it does not
claim uniform protection across platforms. From strongest to weakest:

| Level | Meaning | Honest caveat |
|---|---|---|
| `HARD` | Current process/user cannot remove protection without privilege escalation. | Requires `chwrite lock --hard` explicitly; chwrite never invokes `sudo` itself. |
| `ENFORCED` | OS actively rejects normal write attempts. | The same user can deliberately remove the protection (it's not privilege-gated). |
| `READONLY` | Ordinary write permission bits removed. | A process running as the file owner can generally just restore write permission. |
| `VERIFY` | No write prevention. | chwrite detects the modification later, at commit/push time, not before it happens. |
| `UNPROTECTED` | No protection applied/active. | File is exactly as normal Git would leave it. |

## Command reference

```text
chwrite init                  create .write_protect in the current repo
chwrite add <pathspec>        add a protect rule
chwrite remove <pathspec>     remove a protect rule

chwrite apply                 (re)apply protection per .write_protect (idempotent)
chwrite lock                  apply the default (unprivileged) protection level
chwrite lock --hard           apply HARD protection (may require sudo; chwrite
                               prints the exact command, it never runs it)
chwrite lock <path> --message "..."
                               ad hoc local-only lock, not written to policy
chwrite lock <path> --deny-user <name>
chwrite lock <path> --deny-group <name>
                               ad hoc scoped lock: deny only the named
                               user/group (comma-separated for multiple),
                               instead of blocking everyone
chwrite unlock <path>         remove protection from one file
chwrite unlock --all          remove protection from all protected files
chwrite unlocked -- <cmd>     unlock, run <cmd>, reapply protection afterward
                               regardless of <cmd>'s exit status, then exit
                               with <cmd>'s exit code

chwrite status                show LEVEL / BACKEND / FILE for every protected
                               file, inspecting actual OS state (not cached)
chwrite verify                check protected files for modification/deletion/
                               replacement since last apply; exit 0 valid,
                               1 violation, 2 config/runtime error
chwrite check-path <path>     fast scriptable check for external tools/hooks;
                               exit 0 unprotected, 1 protected (message on
                               stderr), 2 config/runtime error
chwrite doctor                diagnose install/backend/hook health
```

`chwrite-setup` (separate binary, section "Install" above — one-time per
machine, never invoked by `chwrite` itself or its generated git hooks):

```text
chwrite-setup install                 one-time per-user install + global Git hooks
chwrite-setup install --claude-hook   add a repo-committed Claude Code PreToolUse
                                       hook that runs `chwrite check-path`, additive
                                       to any existing .claude/settings.json hooks
chwrite-setup uninstall               remove hooks and restore original file state
```

Running `chwrite apply` any number of times produces the same result as
running it once.

## Per-OS backend

| OS | Backend | Command | Level |
|---|---|---|---|
| macOS | BSD file flags | `chflags uchg` / `chflags nouchg` | `ENFORCED` |
| Linux (default) | POSIX mode bits | `chmod a-w` | `READONLY` |
| Linux (`lock --hard`) | ext-family immutable attribute | `chattr +i` | `HARD` (needs root or `CAP_LINUX_IMMUTABLE`) |
| Windows | NTFS ACLs | explicit deny ACE via `icacls.exe` | `ENFORCED` |
| Other POSIX/BSD | POSIX mode bits | `os.chmod` (strip write bits) | `READONLY` |
| Unrecognized OS | Git-hash baseline + hooks only | no write prevention | `VERIFY` |
| macOS (`deny-user`/`deny-group`) | NFSv4-style ACL deny entry | `chmod +a`/`-a` | `ENFORCED` |
| Linux (`deny-user`/`deny-group`) | POSIX ACL entry | `setfacl -m u:<name>:0` / `g:<name>:0` | `ENFORCED` (deny-group best-effort, see section 29.1) |
| Windows (`deny-user`/`deny-group`) | NTFS ACL, arbitrary named SID | explicit deny ACE via `icacls.exe` | `ENFORCED` |

Runtime state (backend used, original mode, lock status) lives in
`.git/chwrite/state.json` and is never committed. `chwrite status` always
re-inspects real OS state rather than trusting that file.

## What this does NOT protect against

* A malicious or misbehaving process running **as the same OS user/account**
  that owns the protected files. `ENFORCED` and `READONLY` protections can be
  deliberately removed by that same user — that's the whole point of the
  level names, not a bug.
* A malicious process running as **root/Administrator**. Root can clear
  macOS `uchg`, Linux immutable attributes (with `CAP_LINUX_IMMUTABLE`), and
  Windows DACLs, because the file owner (or root) always retains authority
  over an object's own security descriptor.
* Filesystems or environments with no immutable-file primitive at all —
  there, chwrite only offers `VERIFY` (detect after the fact via Git hooks),
  not prevention.
* Anything if chwrite itself isn't installed on the machine doing the
  modifying — a protected repo cloned somewhere without chwrite installed
  gets zero enforcement (`.write_protect` is just inert data to Git and any tool
  that doesn't understand it).
* The `chwrite-setup install --claude-hook` PreToolUse hook is not itself a
  security boundary — it's a legible pre-write explanation layered on top.
  OS-level enforcement (the levels above) is what actually stops the write;
  anything that isn't Claude Code invoking `Edit`/`Write`/etc. never touches
  that hook at all.

Full threat model and rationale: [`SPEC.md`](./SPEC.md) section 3.

## Distribution

Two single files, standard library only: [`chwrite.py`](./chwrite.py) (hot
path) and [`chwrite-setup.py`](./chwrite-setup.py) (one-time setup — see
"Install" above). Optional launchers in this repo, one pair per binary:
[`chwrite`](./chwrite)/[`chwrite.cmd`](./chwrite.cmd) and
[`chwrite-setup`](./chwrite-setup)/[`chwrite-setup.cmd`](./chwrite-setup.cmd).
Every launcher just locates its matching `.py` file and exec/invokes Python
on it, passing args and exit code through unchanged — no logic lives in any
launcher. Also installable via pip/pipx/Homebrew/npm/AUR/apt — see
[`packaging/README.md`](./packaging/README.md) for what's live vs.
wired-but-pending on each.

## Development

`.claude/settings.json` enables two Claude Code project plugins for this
repo: `swe-project-plugin-pack-python` and `swe-project-plugin-pack-shell`
(both `@awad-marketplace`). Both are thin per-language supersets of the same
base `swe-project-plugin-pack` — each of their `hooks.json` independently
registers the *identical* 9 shared base hooks (`auto-push-after-commit.sh`,
`worktree-cleanup-on-merge.sh`, `conventional-commits-guard.sh`,
`semver-guard.sh`, `credential-exfil-guard.sh`, `semgrep-sast-gate.sh`,
`osv-scanner-gate.sh`, `snyk-gate.sh`, `primary-checkout-mutation-guard.sh`,
`pre-write-protected-branch-guard.sh`) by symlinking to the same underlying
script files, in addition to each pack's own language-specific hooks. With
both packs enabled at once (needed here since this repo has both `.py` and
`.sh` files), **those 9 shared hooks fire twice per matching Bash/Write/Edit
call** — confirmed by inspecting both packs' `hooks.json` (identical
`readlink -f` targets). This is wasteful but not destructive: none of the
9 shared hooks performs a mutating action itself (push, branch delete,
worktree removal) — they only ever print an instruction for the agent to
run manually, so a double-fire means duplicate identical stderr text and,
for `semgrep-sast-gate.sh`/`osv-scanner-gate.sh`/`snyk-gate.sh`, a doubled
external-tool invocation (2x commit-time latency for those scans), not a
double push/delete/commit.

Two caveats found while verifying these hooks against this repo's first
commit:

* `semgrep-sast-gate.sh` has no gap between "semgrep couldn't run" and
  "semgrep found something" — any non-zero exit is treated as a blocking
  finding. In a sandboxed environment where outbound access to
  `semgrep.dev` is blocked (rule packs `p/security-audit`/`p/secrets` can't
  be fetched), this hook hard-blocks with `BLOCKED: semgrep found a
  security finding` even though nothing was actually scanned. Contrast with
  `osv-scanner-gate.sh`, which explicitly treats any exit code other than
  0/1 as "not a finding" and warns instead of blocking. If this hits you,
  it isn't a real finding — either restore network access or use the
  documented escape hatch: `SEMGREP_SKIP_REASON="..." git commit ...`.
* `python-line-count-guard.sh` (600-line ceiling) legitimately flags
  [`chwrite.py`](./chwrite.py) (2600+ lines — the generated single-file
  bundle, see [`SPEC.md`](./SPEC.md) section 26) and `tests/test_cli.py`
  (770+ lines). Both are accepted exceptions for this repo rather than bugs
  to fix by splitting: commit with
  `PYTHON_LINE_LIMIT_SKIP_REASON="..." git commit ...`.

## License

[MIT](./LICENSE)
