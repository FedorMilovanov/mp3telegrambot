"""Regression tests for v3 patch 56 — Windows-safe verify_repo output."""

from pathlib import Path


def test_verify_repo_summary_is_ascii_safe_for_windows_cp1251():
    src = Path("tools/verify_repo.py").read_text(encoding="utf-8")
    assert 'mark = "OK" if r.ok else "FAIL"' in src
    assert "✅" not in src
    assert "❌" not in src
    assert "Repository verification passed" in src
