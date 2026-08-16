from __future__ import annotations

from pathlib import Path

from core.media_title_policy import canonical_delivery_filename, canonical_media_title

ROOT = Path(__file__).resolve().parents[1]


def test_uppercase_one_letter_conjunction_is_not_an_acronym() -> None:
    assert canonical_media_title("Сила И Достоинство Благочестивой Женщины - Джон Пайпер") == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"


def test_all_internal_russian_service_words_are_lowercase() -> None:
    assert canonical_media_title("Вопросы И Ответы О Браке И Семье") == "Вопросы и Ответы о Браке и Семье"
    assert canonical_media_title("И Это Только Начало") == "И Это Только Начало"
    assert canonical_media_title("Путь К Богу Через Христа") == "Путь к Богу через Христа"


def test_acronyms_and_internal_case_are_preserved() -> None:
    assert canonical_media_title("Q&A О LSB И МакАртуре") == "Q&A о LSB и МакАртуре"


def test_title_policy_preserves_semantic_punctuation() -> None:
    assert canonical_media_title("Сомнение — Это Не Просто Слабость - Пол Вошер") == "Сомнение — Это не Просто Слабость - Пол Вошер"
    assert canonical_media_title("Сомнение – Это Не Просто Слабость - Пол Вошер") == "Сомнение – Это не Просто Слабость - Пол Вошер"


def test_historical_manifest_filename_is_fixed_without_rerender() -> None:
    filename = "Сила И Достоинство Благочестивой Женщины - Джон Пайпер — только русский голос.mp4"
    assert canonical_delivery_filename(filename) == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер — только русский голос.mp4"


def test_core_title_owner_calls_canonical_policy_directly() -> None:
    import core.text_utils as text_utils
    assert text_utils.title_case_fragment("the power of grace") == "The Power of Grace"
    assert text_utils.title_case_fragment("Сила И Достоинство") == "Сила и Достоинство"
    assert text_utils.title_case_fragment("Сомнение — Это Не Слабость") == "Сомнение — Это не Слабость"


def test_title_policy_is_source_owned_across_public_surfaces() -> None:
    expected = {
        "core/text_utils.py": "canonical_media_title",
        "services/dub_studio.py": "canonical_media_title",
        "services/dub_studio_runtime.py": "canonical_media_title",
        "handlers/dub_delivery.py": "canonical_delivery_filename",
        "services/livedub_output_policy.py": "canonical_media_title",
        "tools/voxcpm2/generic_short_production.py": "canonical_media_title",
    }
    for rel, marker in expected.items():
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert marker in source
    manifest = (ROOT / "services/runtime_manifest.py").read_text(encoding="utf-8")
    assert "dub-title-policy" not in manifest
    assert "install_dub_title_policy" not in manifest
