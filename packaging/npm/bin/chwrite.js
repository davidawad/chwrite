#!/usr/bin/env node
'use strict';

// chwrite - npm bin shim.
//
// This is intentionally the *only* JS in the package. chwrite has no JS
// implementation and no runtime npm dependencies (SPEC.md section 31.2);
// this file's sole job is to find a Python 3 interpreter on PATH and exec
// the bundled `chwrite.py` (shipped alongside this file, see ../chwrite.py)
// against it, passing through argv and propagating the child's exit code.
//
// Using a single cross-platform Node shim (rather than a POSIX shell script
// plus a separate .cmd) means npm's own bin-linking (which produces a
// generated .cmd/.ps1 wrapper around this file on Windows automatically)
// is enough - we don't need to hand-maintain a second shell dialect.

const { execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const SCRIPT_PATH = path.join(__dirname, '..', 'chwrite.py');

// Candidate interpreter commands, in preference order. `py` is the Windows
// launcher and needs an explicit `-3` to select Python 3 (it can otherwise
// resolve to a Python 2 install on old systems).
const CANDIDATES = process.platform === 'win32' ? ['python3', 'python', 'py'] : ['python3', 'python'];

function findPython() {
  for (const candidate of CANDIDATES) {
    try {
      execFileSync(candidate, ['--version'], { stdio: 'ignore' });
      return candidate;
    } catch {
      // Not found or not runnable - try the next candidate.
    }
  }
  return null;
}

function main() {
  if (!fs.existsSync(SCRIPT_PATH)) {
    process.stderr.write(`chwrite: bundled chwrite.py not found at ${SCRIPT_PATH}\n`);
    process.exit(2);
  }

  const python = findPython();
  if (!python) {
    const tried = CANDIDATES.join(', ');
    process.stderr.write(
      `chwrite: no Python 3 interpreter found on PATH (tried: ${tried}).\n` +
        'chwrite requires Python 3.11+. Install it and re-run.\n',
    );
    process.exit(2);
  }

  const forwardedArgs = process.argv.slice(2);
  const args = python === 'py' ? ['-3', SCRIPT_PATH, ...forwardedArgs] : [SCRIPT_PATH, ...forwardedArgs];

  try {
    execFileSync(python, args, { stdio: 'inherit' });
  } catch (err) {
    if (typeof err.status === 'number') {
      process.exit(err.status);
    }
    if (err.signal) {
      process.stderr.write(`chwrite: terminated by signal ${err.signal}\n`);
      process.exit(1);
    }
    process.stderr.write(`chwrite: failed to execute '${python}': ${err.message}\n`);
    process.exit(2);
  }
  process.exit(0);
}

main();
