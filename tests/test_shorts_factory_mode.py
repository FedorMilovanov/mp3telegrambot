from handlers.mode_command import MODE_DESCRIPTIONS, MODE_LABELS, VALID_MODES
from pipelines.shorts_factory import (
    _source_needs_translation,
    _translation_backend,
)


def test_shorts_factory_is_exposed_as_persistent_mode():
    assert "shorts_max" in VALID_MODES
    assert "SHORTS FACTORY MAX" in MODE_LABELS["shorts_max"]
    description = MODE_DESCRIPTIONS["shorts_max"]
    assert "Яндекс" in description
    assert "без собственного нейроперевода" in description


def test_non_russian_source_requires_translation():
    assert _source_needs_translation({"language": "en", "title": "A sermon"}) is True
    assert _source_needs_translation({"language": "fr", "title": "Un sermon"}) is True
    assert _source_needs_translation({"language": "ru", "title": "Проповедь"}) is False


def test_unknown_language_uses_title_script_as_conservative_signal():
    assert _source_needs_translation({"language": "", "title": "The Gospel"}) is True
    assert _source_needs_translation({"language": "", "title": "Евангелие"}) is False


def test_translation_backend_defaults_to_yandex_only(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_TRANSLATION_BACKEND", raising=False)
    assert _translation_backend() == "yandex_live"

    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "yandex")
    assert _translation_backend() == "yandex_live"

    monkeypatch.setenv("SHORTS_FACTORY_TRANSLATION_BACKEND", "neural_future")
    assert _translation_backend() == "neural_future"
