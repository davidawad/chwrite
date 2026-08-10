"""Unit tests for the VERIFY-only fallback backend (SPEC.md 14, 27)."""

from __future__ import annotations

from chwrite.backends.unknown import protect_unknown, query_unknown, unprotect_unknown


def test_protect_unknown_is_a_verify_only_noop(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")

    result = protect_unknown(str(target), hard=False)

    assert result == {"backend": "verify-only", "level": "VERIFY", "hard": False}
    assert target.read_text() == "hi"  # untouched


def test_protect_unknown_ignores_hard_flag(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    result = protect_unknown(str(target), hard=True)
    assert result["level"] == "VERIFY"


def test_unprotect_unknown_is_a_noop(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    unprotect_unknown(str(target), {"backend": "verify-only", "original_mode": None})
    assert target.read_text() == "hi"


def test_query_unknown_existing_file_is_verify(tmp_path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hi")
    level, backend = query_unknown(str(target))
    assert level == "VERIFY"
    assert backend == "verify-only"


def test_query_unknown_missing_file(tmp_path) -> None:
    level, backend = query_unknown(str(tmp_path / "gone.txt"))
    assert level == "MISSING"
    assert backend is None
