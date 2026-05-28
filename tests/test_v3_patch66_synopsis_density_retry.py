"""Regression tests for v3 patch 66 — non-fatal Synopsis density retry."""

from pathlib import Path

from core.synopsis_quality import (
    audit_synopsis_density,
    should_retry_synopsis_density,
    synopsis_density_score,
)


def test_synopsis_density_retry_policy_only_for_long_thin_outputs():
    issues = audit_synopsis_density([{"title": "A", "time": "0:00", "content": "short"}], 3600)
    assert should_retry_synopsis_density(issues, 3600) is True
    assert should_retry_synopsis_density(issues, 20 * 60) is False


def test_synopsis_density_score_prefers_fuller_output():
    thin = [{"title": "A", "time": "0:00", "content": "x" * 100}]
    full = [{"title": str(i), "time": f"{i}:00", "content": "x" * 1000} for i in range(10)]
    assert synopsis_density_score(full, 3600) > synopsis_density_score(thin, 3600)


def test_telegraph_wires_nonfatal_density_retry():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "def _synopsis_density_retry_enabled" in src
    assert "SYNOPSIS_DENSITY_RETRY" in src
    assert "should_retry_synopsis_density" in src
    assert "density retry accepted" in src
    assert "density retry failed non-fatally" in src
    assert "SynopsisDensityRetry" in src
