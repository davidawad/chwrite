# Homebrew formula for chwrite.
#
# NOT YET INSTALLABLE VIA `brew install`. This formula is written and
# validated as far as possible in the absence of a real GitHub release
# (see packaging/README.md, "Homebrew" section, for what's still pending
# and why). Two placeholders below MUST be filled in once this repo has
# an actual GitHub remote and a published v1.0.0 release tarball:
#
#   1. <GITHUB_OWNER>/<GITHUB_REPO> in the `homepage`/`url` below.
#   2. PLACEHOLDER_SHA256 in the `sha256` below - compute it with:
#
#        curl -L -o chwrite-1.0.0.tar.gz \
#          https://github.com/<GITHUB_OWNER>/<GITHUB_REPO>/archive/refs/tags/v1.0.0.tar.gz
#        shasum -a 256 chwrite-1.0.0.tar.gz
#
#      and paste the resulting 64-character hex digest in place of
#      PLACEHOLDER_SHA256. Repeat for every future release (or let CI do
#      it once packaging/README.md's "Homebrew" section's automation is
#      wired up against a real tap repo).
class Chwrite < Formula
  desc "Protect declared files in a Git repo from unwanted modification"
  homepage "https://github.com/<GITHUB_OWNER>/<GITHUB_REPO>"
  url "https://github.com/<GITHUB_OWNER>/<GITHUB_REPO>/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "MIT"

  # chwrite.py is pure stdlib (SPEC.md section 1/19) and requires Python
  # 3.11+ only for its optional read-only `tomllib` TOML-policy support
  # (SPEC.md section 24.1). python@3.12 is the current non-EOL default
  # `python@3.x` formula in homebrew-core as of this writing; bump this
  # alongside homebrew-core's own default bump, same as any other
  # Python-based formula.
  depends_on "python@3.12"

  def install
    # `bin.install "chwrite.py"` was considered and rejected: it would
    # install the file under the name "chwrite.py" (wrong command name -
    # `chwrite`, not `chwrite.py`, is the intended CLI entry point per
    # SPEC.md section 6/19), and it would leave whatever shebang is
    # already baked into the committed chwrite.py (`#!/usr/bin/env
    # python3`) to resolve `python3` via `$PATH` at run time - which,
    # inside a Homebrew install, is not guaranteed to be this formula's
    # `python@3.12` dependency (there may be no `python3` on PATH at
    # all, or a *different* python3 earlier on PATH than this formula's
    # keg-only one).
    #
    # Instead: install the actual interpreted script into `libexec`
    # (Homebrew convention for "real" files a wrapper points at, keeping
    # `bin` reserved for the thin executable wrapper - see
    # https://docs.brew.sh/Python-for-Formula-Authors), then generate a
    # small wrapper script in `bin` that invokes this formula's own
    # `python@3.12` explicitly against the libexec copy. This is the
    # documented Homebrew idiom for a dependency-free, non-setuptools
    # Python script (`(bin/"name").write <<~EOS ... EOS`), and is
    # preferred here over adapting the repo's own POSIX `chwrite` shim
    # (repo root's `chwrite`) because that shim's job is to search
    # `$PATH`/`$HOME` for a `python3` and a `chwrite.py` at *run time*
    # for non-packaged (curl'd/cloned) installs - useful when there is
    # no package manager pinning the interpreter, but redundant and
    # strictly weaker than a hardcoded interpreter path once Homebrew
    # itself is already pinning both files to known, fixed locations at
    # install time. Fewer moving parts, one obvious interpreter, no
    # runtime PATH search.
    libexec.install "chwrite.py"

    python3 = Formula["python@3.12"].opt_bin/"python3.12"
    (bin/"chwrite").write <<~EOS
      #!/bin/bash
      exec "#{python3}" "#{libexec}/chwrite.py" "$@"
    EOS
  end

  test do
    system bin/"chwrite", "--help"
  end
end
