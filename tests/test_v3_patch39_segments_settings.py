"""Regression tests for v3 patch 39 — segment settings UI and gates."""

from pathlib import Path

from core.database import SETTINGS_DEFAULTS, SETTINGS_GROUPS, SETTINGS_LABELS


def test_segment_settings_are_declared_and_grouped():
    assert SETTINGS_DEFAULTS["segments"] is False
    assert SETTINGS_DEFAULTS["segments_render"] is True
    assert SETTINGS_DEFAULTS["segments_batch_render"] is False
    assert SETTINGS_LABELS["segments"] == "🧩 Сегменты"
    assert SETTINGS_LABELS["segments_render"] == "🎞 Рендер сегментов"
    assert SETTINGS_LABELS["segments_batch_render"] == "📦 Пакетная нарезка"
    groups = dict(SETTINGS_GROUPS)
    assert "🧩 Сегменты" in groups
    assert groups["🧩 Сегменты"] == ["segments", "segments_render", "segments_subtitles", "segments_batch_render"]


def test_segments_commands_check_settings_before_work():
    commands = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert 'await asettings_get("segments")' in commands
    assert 'await asettings_get("segments_render")' in commands
    assert 'await asettings_get("segments_batch_render")' in commands
    assert "Сегменты выключены в настройках" in commands
    assert "Рендер сегментов выключен" in commands


def test_settings_keyboard_will_render_segment_buttons():
    callbacks = Path("handlers/callbacks.py").read_text(encoding="utf-8")
    assert "SETTINGS_GROUPS" in callbacks
    assert "SETTINGS_LABELS" in callbacks
    assert "setting:{key}" in callbacks
