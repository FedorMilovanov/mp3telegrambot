from pathlib import Path

from services.gemini_model_status import classify_gemini_model


ROOT = Path(__file__).resolve().parents[1]


def test_gemini_38_is_deployment_max_quality_route() -> None:
    diagnostic = classify_gemini_model("gemini-3.8-flash")
    assert diagnostic.level == "info"
    assert "max-quality production route" in diagnostic.message


def test_previous_37_route_warns_to_move_to_38() -> None:
    diagnostic = classify_gemini_model("gemini-3.7-flash")
    assert diagnostic.level == "warning"
    assert "gemini-3.8-flash" in diagnostic.message


def test_36_is_unknown_or_legacy_not_current_primary() -> None:
    diagnostic = classify_gemini_model("gemini-3.6-flash")
    assert diagnostic.level == "warning"
    assert "gemini-3.8-flash" in diagnostic.message or "routing policy" in diagnostic.message


def test_35_lite_is_utility_only() -> None:
    diagnostic = classify_gemini_model("gemini-3.5-flash-lite")
    assert diagnostic.level == "warning"
    assert "utility-only" in diagnostic.message
    assert "gemini-3.8-flash" in diagnostic.message


def test_regular_35_is_not_part_of_source_owned_routing() -> None:
    diagnostic = classify_gemini_model("gemini-3.5-flash")
    assert diagnostic.level == "warning"
    assert "не используется source-owned routing" in diagnostic.message


def test_latest_alias_warns_about_hot_swap() -> None:
    diagnostic = classify_gemini_model("gemini-flash-latest")
    assert diagnostic.level == "warning"
    assert "плавающий alias" in diagnostic.message
    assert "gemini-3.8-flash" in diagnostic.message


def test_preview_models_are_not_reported_as_stable() -> None:
    flash = classify_gemini_model("gemini-3-flash-preview")
    pro = classify_gemini_model("gemini-3.1-pro-preview")
    assert flash.level == "warning"
    assert "gemini-3.8-flash" in flash.message
    assert pro.level == "warning"
    assert "gemini-3.8-flash" in pro.message


def test_scheduled_ga_migration_has_exact_deadline() -> None:
    diagnostic = classify_gemini_model("gemini-3.1-flash-lite")
    assert diagnostic.level == "warning"
    assert "2027-05-07" in diagnostic.message
    assert "gemini-3.5-flash-lite" in diagnostic.message


def test_shutdown_model_is_error() -> None:
    diagnostic = classify_gemini_model("gemini-2.0-flash")
    assert diagnostic.level == "error"
    assert "gemini-3.8-flash" in diagnostic.message


def test_legacy_25_has_exact_migration_deadline() -> None:
    diagnostic = classify_gemini_model("gemini-2.5-flash")
    assert diagnostic.level == "warning"
    assert "2026-10-16" in diagnostic.message


def test_main_uses_classified_capabilities_and_current_catalog() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "classify_gemini_model" in source
    assert "_required_tools" in source
    assert "_optional_tools" in source
    assert "часть функций молча деградирует" not in source
    assert '"gemini-3.1-flash-lite-preview"' not in source
