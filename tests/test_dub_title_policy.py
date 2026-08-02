from __future__ import annotations

from pathlib import Path

from services import dub_title_policy
from services.dub_title_policy import (
    canonical_delivery_filename,
    canonical_media_title,
)
from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES


ROOT = Path(__file__).resolve().parents[1]


def test_uppercase_one_letter_conjunction_is_not_an_acronym() -> None:
    assert (
        canonical_media_title(
            "Сила И Достоинство Благочестивой Женщины - Джон Пайпер"
        )
        == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"
    )


def test_all_internal_russian_service_words_are_lowercase() -> None:
    assert (
        canonical_media_title("Вопросы И Ответы О Браке И Семье")
        == "Вопросы и Ответы о Браке и Семье"
    )
    assert canonical_media_title("И Это Только Начало") == "И Это Только Начало"
    assert canonical_media_title("Путь К Богу Через Христа") == "Путь к Богу через Христа"


def test_acronyms_and_internal_case_are_preserved() -> None:
    assert canonical_media_title("Q&A О LSB И МакАртуре") == "Q&A о LSB и МакАртуре"


def test_title_policy_changes_case_but_not_semantic_punctuation() -> None:
    assert canonical_media_title(
        "Сомнение — Это Не Просто Слабость - Пол Вошер"
    ) == "Сомнение — Это не Просто Слабость - Пол Вошер"
    assert canonical_media_title(
        "Сомнение – Это Не Просто Слабость - Пол Вошер"
    ) == "Сомнение – Это не Просто Слабость - Пол Вошер"


def test_historical_manifest_filename_is_fixed_without_rerender() -> None:
    filename = (
        "Сила И Достоинство Благочестивой Женщины - Джон Пайпер "
        "— только русский голос.mp4"
    )
    assert canonical_delivery_filename(filename) == (
        "Сила и Достоинство Благочестивой Женщины - Джон Пайпер "
        "— только русский голос.mp4"
    )


def test_core_patch_preserves_existing_english_title_case() -> None:
    import core.text_utils as text_utils

    dub_title_policy._patch_core_title_case()
    assert text_utils.title_case_fragment("the power of grace") == "The Power of Grace"
    assert (
        text_utils.title_case_fragment("Сила И Достоинство")
        == "Сила и Достоинство"
    )
    assert (
        text_utils.title_case_fragment("Сомнение — Это Не Слабость")
        == "Сомнение — Это не Слабость"
    )


def test_clean_routes_keep_title_policy_and_correct_resume_mode() -> None:
    for name in (
        "generic_clean_gemini_runtime.py",
        "generic_clean_custom_runtime.py",
    ):
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "install_voxcpm_title_policy" in source
        assert "force_fresh=True" in source
        assert "semantic_tts_guard_v4.install" not in source
        assert "runpy.run_path" not in source

    direct = (
        ROOT / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py"
    ).read_text(encoding="utf-8")
    assert "install_voxcpm_title_policy" in direct
    assert "force_fresh=False" in direct
    assert "Keeping this False makes a late failed segment resumable" in direct
    assert "semantic_tts_guard_v4.install" not in direct
    assert "runpy.run_path" not in direct


def test_title_health_accepts_resumable_direct_route() -> None:
    ok, detail = dub_title_policy._title_health_contract(ROOT)

    assert ok, detail
    assert "готовый SRT продолжает проверенные checkpoints" in detail


def test_manifest_installs_title_policy_after_dub_runtime() -> None:
    order = {
        feature.feature_id: index
        for index, feature in enumerate(DEFAULT_RUNTIME_FEATURES)
    }
    assert order["dub-studio-runtime"] < order["dub-title-policy"]
    title_feature = next(
        feature
        for feature in DEFAULT_RUNTIME_FEATURES
        if feature.feature_id == "dub-title-policy"
    )
    assert title_feature.required is True


def test_title_policy_is_runtime_wide_and_health_checked() -> None:
    source = (ROOT / "services" / "dub_title_policy.py").read_text(encoding="utf-8")
    assert "text_utils.sentence_case_russian_title = wrapped" in source
    assert "DubStore._row_project = wrapped" in source
    assert "runtime._undelivered_notification_events = wrapped" in source
    assert "delivery.available_outputs = wrapped" in source
    assert "health.collect_dub_health = wrapped" in source
    assert "output_policy._russian_heading_case = canonical_media_title" in source
