default: ci

build:
    python3 scripts/bundle.py

fmt:
    uv run ruff format .

fmt-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

typecheck:
    uv run pyright

test:
    uv run pytest tests/ --cov=src/chwrite --cov-fail-under=85 -q
    just build-check

# Regenerate chwrite.py to a temp path and diff against the committed one -
# `just ci` must fail if the committed bundle is stale relative to src/chwrite/.
build-check:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    python3 scripts/bundle.py
    cp chwrite.py "$tmp"
    git diff --quiet -- chwrite.py || { echo "chwrite.py is out of date; run 'just build' and commit it"; exit 1; }

audit:
    uv run pip-audit

ci: fmt-check lint typecheck test

# Slow lanes - manual
mutants:
    uv run mutmut run

deadcode:
    uv run vulture src/ --min-confidence 80

all: ci mutants deadcode
