#!/usr/bin/env python3
"""Executable form of SPEC.md section 23's acceptance test (see section 30).

Stdlib-only, cross-platform (macOS / Linux / Windows). Requires nothing
beyond the Python standard library plus `git` and whatever OS-native tool
chwrite itself already shells out to (chflags / chattr / icacls) - no uv,
no pytest, no third-party packages, since CI's `acceptance` job intentionally
skips the dev-tooling setup that `lint-and-unit` uses.

It builds a throwaway git repository under the OS temp dir (never inside
this checkout), writes a `.write_protect` policy, and drives the committed
`chwrite.py` at the repo root through init/apply/status/verify/check-path/
unlock via subprocess - exactly the workflow SPEC.md section 23 describes,
plus the per-message check-path behavior from section 25 and the
idempotency requirement from section 18. Each assertion prints a PASS/FAIL/
SKIP line as it runs, so this script doubles as documentation-by-execution.

Exit status:
    0   every assertion that ran passed (some may have printed SKIP)
    1   at least one assertion failed
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHWRITE_PY = REPO_ROOT / "chwrite.py"
PROTECT_MESSAGE = "do not touch this file - managed by acceptance test"

_pass_count = 0
_fail_count = 0
_skip_count = 0
_failures: list[str] = []


def ok(label: str) -> None:
    global _pass_count  # noqa: PLW0603
    _pass_count += 1
    print(f"PASS: {label}")


def bad(label: str, detail: str = "") -> None:
    global _fail_count  # noqa: PLW0603
    _fail_count += 1
    msg = f"FAIL: {label}" + (f"\n      detail: {detail}" if detail else "")
    print(msg)
    _failures.append(f"{label}" + (f" ({detail})" if detail else ""))


def skip(label: str, reason: str) -> None:
    global _skip_count  # noqa: PLW0603
    _skip_count += 1
    print(f"SKIP: {label} ({reason})")


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        ok(label)
    else:
        bad(label, detail)


def run_chwrite(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Invoke the committed chwrite.py with the current interpreter.

    Using sys.executable (rather than hard-coding python/python3) is what
    lets this one script work unmodified whether CI invokes it as
    `python scripts/acceptance_test.py` (Windows) or
    `python3 scripts/acceptance_test.py` (macOS/Linux).
    """
    return subprocess.run(
        [sys.executable, str(CHWRITE_PY), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr}")
    return proc


def make_throwaway_repo() -> Path:
    """A fresh git repo under the OS temp dir - never under REPO_ROOT.

    tempfile.mkdtemp() resolves to the OS temp dir (TMPDIR/TEMP/TMP), which
    is never inside a project checkout; the assertion below is a defensive
    double-check, not the primary safety mechanism.
    """
    tmp = Path(tempfile.mkdtemp(prefix="chwrite-acceptance-"))
    resolved_tmp = tmp.resolve()
    resolved_repo_root = REPO_ROOT.resolve()
    if resolved_repo_root == resolved_tmp or resolved_repo_root in resolved_tmp.parents:
        raise RuntimeError(
            f"refusing to run the acceptance test inside the project checkout: {tmp}"
        )
    run_git(["init", "-q"], tmp)
    run_git(["config", "user.email", "acceptance-test@chwrite.invalid"], tmp)
    run_git(["config", "user.name", "chwrite acceptance test"], tmp)
    (tmp / "protected.txt").write_text("original content\n", encoding="utf-8")
    (tmp / "unprotected.txt").write_text("nothing special\n", encoding="utf-8")
    (tmp / ".write_protect").write_text(
        f'version 1\n\nprotect protected.txt message="{PROTECT_MESSAGE}"\n',
        encoding="utf-8",
    )
    run_git(["add", "-A"], tmp)
    run_git(["commit", "-q", "-m", "initial commit"], tmp)
    return tmp


def try_direct_write(path: Path, content: str) -> tuple[bool, str]:
    """Attempt a plain Python write; returns (succeeded, error-detail)."""
    try:
        with path.open("w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    else:
        return True, ""


def bypass_os_protection(path: Path) -> tuple[bool, str]:  # noqa: PLR0911
    """Remove the OS-level protection out-of-band, without going through
    `chwrite unlock` - simulating a careless/adversarial process that reaches
    for the raw OS primitive directly (SPEC.md section 17: "protection flags
    that have been removed").

    Uses exactly the mechanism the matching backend module documents
    (SPEC.md sections 10-12): `chflags nouchg` on macOS, `chattr -i` +
    restoring the owner-write bit on Linux (chwrite's own default `apply`
    without --hard only sets the mode bit, never +i - clearing +i first is
    harmless/no-op in that default case and correct if a prior `--hard` run
    left it set), and removing chwrite's own icacls deny ACE on Windows
    (mirrors unprotect_windows() in src/chwrite/backends/windows.py).

    Returns (attempted, reason-not-attempted).
    """
    system = platform.system()
    if system == "Darwin":
        if not shutil.which("chflags"):
            return False, "chflags not found on this macOS system"
        subprocess.run(["chflags", "nouchg", str(path)], capture_output=True, check=False)
        return True, ""
    if system == "Linux":
        chattr = shutil.which("chattr")
        if chattr:
            subprocess.run([chattr, "-i", str(path)], capture_output=True, check=False)
        try:
            current_mode = stat.S_IMODE(path.lstat().st_mode)
            path.chmod(current_mode | stat.S_IWUSR)
        except OSError as e:
            return False, f"could not restore write bit: {e}"
        return True, ""
    if system == "Windows":
        icacls = shutil.which("icacls") or shutil.which("icacls.exe")
        if not icacls:
            return False, "icacls not found on this Windows system"
        user = os.environ.get("USERNAME", "")
        subprocess.run([icacls, str(path), "/remove:d", user], capture_output=True, check=False)
        try:
            current_mode = stat.S_IMODE(path.lstat().st_mode)
            path.chmod(current_mode | stat.S_IWRITE)
        except OSError:
            pass  # best-effort; the icacls deny removal is what actually matters here
        return True, ""
    return False, f"no bypass mechanism implemented for platform {system!r}"


def run_acceptance_checks(repo: Path) -> None:
    protected = repo / "protected.txt"

    # --- apply --------------------------------------------------------
    proc = run_chwrite(["apply"], repo)
    check(proc.returncode == 0, "apply exits 0", f"rc={proc.returncode} stderr={proc.stderr!r}")

    proc = run_chwrite(["verify"], repo)
    check(
        proc.returncode == 0,
        "verify exits 0 immediately after apply (nothing tampered with yet)",
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}",
    )

    proc = run_chwrite(["status"], repo)
    check(proc.returncode == 0, "status exits 0 after apply", f"stdout={proc.stdout!r}")
    status_after_first_apply = proc.stdout

    # --- (a) direct write fails while protection is active -------------
    succeeded, detail = try_direct_write(protected, "changed-while-protected")
    check(
        not succeeded,
        "direct Python write to protected file fails while protection is active",
        detail or "write unexpectedly succeeded",
    )
    if succeeded:
        # Keep going with a known-good baseline rather than let one
        # failure cascade into unrelated failures below.
        protected.write_text("original content\n", encoding="utf-8")

    # --- (b) check-path -------------------------------------------------
    proc = run_chwrite(["check-path", "protected.txt"], repo)
    check(
        proc.returncode == 1,
        "check-path exits 1 for the protected file",
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}",
    )
    check(
        PROTECT_MESSAGE in proc.stderr,
        "check-path prints the rule's custom message to stderr",
        f"stderr={proc.stderr!r}",
    )

    proc = run_chwrite(["check-path", "unprotected.txt"], repo)
    check(
        proc.returncode == 0,
        "check-path exits 0 for an unprotected file",
        f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}",
    )

    # --- (c) verify detects an out-of-band flag removal + edit ----------
    attempted, reason = bypass_os_protection(protected)
    if not attempted:
        skip("verify detects out-of-band flag removal + edit", reason)
    else:
        wrote, detail = try_direct_write(protected, "changed-out-of-band")
        if not wrote:
            skip(
                "verify detects out-of-band flag removal + edit",
                f"OS bypass ran but direct write still failed afterwards: {detail}",
            )
        else:
            proc = run_chwrite(["verify"], repo)
            check(
                proc.returncode == 1,
                "verify exits 1 after an out-of-band flag removal + edit",
                f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            check(
                "violation" in proc.stderr.lower(),
                "verify reports a violation on stderr",
                f"stderr={proc.stderr!r}",
            )

    # --- (d) unlock restores writability; apply re-protects -------------
    proc = run_chwrite(["unlock", "protected.txt"], repo)
    check(proc.returncode == 0, "unlock exits 0", f"rc={proc.returncode} stderr={proc.stderr!r}")

    succeeded, detail = try_direct_write(protected, "changed-after-unlock")
    check(succeeded, "direct write succeeds after unlock", detail)

    proc = run_chwrite(["apply"], repo)
    check(
        proc.returncode == 0,
        "apply (re-protect after unlock) exits 0",
        f"rc={proc.returncode} stderr={proc.stderr!r}",
    )

    succeeded, detail = try_direct_write(protected, "changed-after-reapply")
    check(
        not succeeded,
        "direct write fails again after re-apply",
        detail or "write unexpectedly succeeded",
    )

    # --- (e) idempotency: apply x10 matches apply x1 (SPEC.md section 18)
    for i in range(9):
        proc = run_chwrite(["apply"], repo)
        check(
            proc.returncode == 0,
            f"apply (idempotency run {i + 2}/10) exits 0",
            f"rc={proc.returncode} stderr={proc.stderr!r}",
        )

    proc = run_chwrite(["status"], repo)
    check(proc.returncode == 0, "status exits 0 after 10x apply", f"stdout={proc.stdout!r}")
    check(
        proc.stdout == status_after_first_apply,
        "status output after apply x10 matches status output after apply x1 (idempotency)",
        "status text differs:\n"
        f"--- after 1 apply ---\n{status_after_first_apply}\n"
        f"--- after 10 applies ---\n{proc.stdout}",
    )


def summarize() -> int:
    print()
    print(f"{_pass_count} passed, {_fail_count} failed, {_skip_count} skipped")
    if _fail_count:
        print()
        print("FAILURES:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    return 0


def main() -> int:
    print("chwrite acceptance test (SPEC.md section 23 / 30)")
    print(f"platform: {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"python:   {sys.version.split()[0]} ({sys.executable})")
    print(f"chwrite:  {CHWRITE_PY}")
    print()

    if not CHWRITE_PY.is_file():
        bad("chwrite.py present at repo root", f"not found at {CHWRITE_PY}")
        return summarize()

    repo = make_throwaway_repo()
    print(f"throwaway repo: {repo}")
    print()

    try:
        run_acceptance_checks(repo)
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    return summarize()


if __name__ == "__main__":
    sys.exit(main())
