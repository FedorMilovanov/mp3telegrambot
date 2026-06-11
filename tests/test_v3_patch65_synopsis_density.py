"""Regression tests for v3 patch 65 — duration-aware Synopsis density audit."""

from pathlib import Path

from core.synopsis_quality import audit_synopsis_density, get_synopsis_density_profile


def test_synopsis_density_profiles_scale_for_long_materials():
    short = get_synopsis_density_profile(600)
    medium = get_synopsis_density_profile(2400)
    long = get_synopsis_density_profile(3600)
    very_long = get_synopsis_density_profile(6000)
    assert short.max_tokens < medium.max_tokens < long.max_tokens <= very_long.max_tokens
    assert long.sections == "10-16"
    assert long.total_chars == "9000"
    assert very_long.sections == "12-20"


def test_synopsis_density_audit_flags_too_thin_long_synopsis():
    sections = [{"title": "A", "time": "0:00", "content": "short"} for _ in range(6)]
    issues = audit_synopsis_density(sections, 3600)
    codes = {i.code for i in issues}
    assert "synopsis_too_few_sections" in codes
    assert "synopsis_too_few_chars" in codes


def test_synopsis_density_audit_flags_low_time_coverage():
    sections = [
        {"title": "A", "time": "0:00", "content": "x" * 1000},
        {"title": "B", "time": "20:00", "content": "x" * 1000},
        {"title": "C", "time": "30:00", "content": "x" * 1000},
        {"title": "D", "time": "35:00", "content": "x" * 1000},
        {"title": "E", "time": "40:00", "content": "x" * 1000},
        {"title": "F", "time": "45:00", "content": "x" * 1000},
        {"title": "G", "time": "46:00", "content": "x" * 1000},
        {"title": "H", "time": "47:00", "content": "x" * 1000},
        {"title": "I", "time": "48:00", "content": "x" * 1000},
    ]
    issues = audit_synopsis_density(sections, 90 * 60)
    assert "synopsis_time_coverage_low" in {i.code for i in issues}


def test_telegraph_wires_synopsis_density_profile_and_audit():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "from core.synopsis_quality import" in src
    assert "get_synopsis_density_profile(_duration)" in src
    assert "_syn_max_tokens" in src
    assert "audit_synopsis_density(sections, _duration)" in src
    assert "density/coverage audit" in src


def test_synopsis_density_unknown_duration_is_not_very_long():
    unknown = get_synopsis_density_profile(0)
    assert unknown.name == "unknown"
    assert unknown.min_sections < get_synopsis_density_profile(7200).min_sections
    assert unknown.min_total_chars < get_synopsis_density_profile(7200).min_total_chars
