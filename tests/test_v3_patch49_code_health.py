"""Regression tests for v3 patch 49 — code/regex health diagnostics."""

from pathlib import Path

from core.code_health import collect_code_health, format_code_health_report


def test_code_health_collects_regex_and_registry_metrics():
    report = collect_code_health(Path("."))
    assert report.files_scanned > 10
    assert report.total_regex_markers > 0
    assert report.source_title_registry_entries > 0
    assert report.typo_replacements > 0
    assert report.top_files


def test_code_health_report_is_admin_html_readout():
    text = format_code_health_report(Path("."))
    assert "Code health" in text
    assert "regex_markers" in text
    assert "Top complexity files" in text
    assert "новые regex" in text
    assert "registry" in text


def test_codehealth_command_is_registered():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    main = Path("main.py").read_text(encoding="utf-8")
    assert "from core.code_health import format_code_health_report" in commands
    assert "async def codehealth_command" in commands
    assert "format_code_health_report" in commands
    assert 'CommandHandler("codehealth"' in main
