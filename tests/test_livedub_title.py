"""Behavioral contracts for LiveDub caption title normalization and routing."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import globals as core_globals
from pipelines import livedub_title as titles
from services import livedub_info


@pytest.fixture(autouse=True)
def clear_title_cache():
    titles._LIVEDUB_TITLE_CACHE.clear()
    yield
    titles._LIVEDUB_TITLE_CACHE.clear()


def _translate(*args, **kwargs):
    return asyncio.run(titles._translate_livedub_title_for_caption(*args, **kwargs))


def _install_main_gemini(monkeypatch, response_text, light_card):
    """Route the lightweight card and main fallback through deterministic fakes."""
    calls = {"sdk": [], "config": [], "models": []}

    class _Models:
        async def generate_content(self, **kwargs):
            calls["sdk"].append(kwargs)
            return SimpleNamespace(text=response_text)

    client = SimpleNamespace(aio=SimpleNamespace(models=_Models()))
    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [client])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "1")

    async def fake_card(title_line, dub_srt_path=None, *, source_url="", force=False):
        assert force is True
        return light_card

    def fake_config(**kwargs):
        calls["config"].append(kwargs)
        return kwargs

    async def fake_router(client_list, fn, model_name=""):
        assert client_list == [client]
        calls["models"].append(model_name)
        return await fn(client_list[0])

    monkeypatch.setattr(livedub_info, "build_livedub_info_card", fake_card)
    monkeypatch.setattr(core_globals, "make_text_config_smart", fake_config)
    monkeypatch.setattr(core_globals, "gemini_generate", fake_router)
    return calls


# Retained cases moved verbatim in behavior from the historical QA collector;
# only the implementation module under test changed.
def test_livedub_caption_title_light_translation(monkeypatch):
    async def fake_card(title_line, dub_srt_path=None, *, source_url="", force=False):
        assert force is True
        return {"youtube_title": "Почему вы диспенсационалист? - Абнер Чау & Кости Хинн"}

    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [object()])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "1")
    monkeypatch.setattr(livedub_info, "build_livedub_info_card", fake_card)
    title, author = _translate(
        "Why Are You a Dispensationalist? - Abner Chou & Costi Hinn",
        "Why Are You a Dispensationalist?",
        "Abner Chou & Costi Hinn",
        "BibleQ&A",
    )
    assert title == "Почему вы диспенсационалист? - Абнер Чау & Кости Хинн"
    assert author == ""
    assert titles._format_livedub_title_line(title, author) == title


def test_main_module_wrapper_uses_main_gemini_clients(monkeypatch):
    """The historical main-module monkeypatch seam is executable, not aliased."""
    import pipelines.main_pipeline as main

    async def fake_card(title_line, dub_srt_path=None, *, source_url="", force=False):
        return {"youtube_title": "Истинная Вера - Пол Вошер"}

    monkeypatch.setattr(main, "GEMINI_CLIENTS", [object()])
    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "1")
    monkeypatch.setattr(livedub_info, "build_livedub_info_card", fake_card)

    result = asyncio.run(
        main._translate_livedub_title_for_caption(
            "True Faith - Paul Washer",
            "",
            "",
            "HeartCry",
        )
    )
    assert result == ("Истинная Вера - Пол Вошер", "")


def test_livedub_caption_uses_existing_analysis_without_extra_gemini(monkeypatch):
    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [])
    title, author = _translate(
        "Why Are You a Dispensationalist? - Abner Chou & Costi Hinn",
        "Why Are You a Dispensationalist?",
        "Abner Chou & Costi Hinn",
        "BibleQ&A",
        analysis_title="Почему вы диспенсационалист?",
        analysis_author="Абнер Чау & Кости Хинн",
    )
    assert title == "Почему вы диспенсационалист?"
    assert author == "Абнер Чау & Кости Хинн"


def test_livedub_title_translate_can_be_disabled_for_metadata_only(monkeypatch):
    class _BadModels:
        async def generate_content(self, **kwargs):
            raise AssertionError("Gemini must not be called when LIVEDUB_TITLE_TRANSLATE=0")

    monkeypatch.setattr(
        titles,
        "GEMINI_CLIENTS",
        [SimpleNamespace(aio=SimpleNamespace(models=_BadModels()))],
    )
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "0")
    title, author = _translate(
        "All Your Deeds Will Be Exposed at the White Throne - Paul Washer",
        "",
        "",
        "HeartCry Missionary Society",
    )
    assert author == "Пол Вошер"
    assert title.startswith("All Your Deeds")


def test_livedub_title_default_uses_light_info_pipeline(monkeypatch):
    async def fake_card(title_line, dub_srt_path=None, *, source_url="", force=False):
        assert force is True
        return {"youtube_title": "Все ваши дела будут вскрыты у Белого престола - Пол Вошер"}

    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [object()])
    monkeypatch.delenv("LIVEDUB_TITLE_TRANSLATE", raising=False)
    monkeypatch.setattr(livedub_info, "build_livedub_info_card", fake_card)
    title, author = _translate(
        "All Your Deeds Will Be Exposed at the White Throne - Paul Washer",
        "",
        "",
        "HeartCry Missionary Society",
    )
    assert title == "Все ваши дела будут вскрыты у Белого престола - Пол Вошер"
    assert author == ""


def test_livedub_title_author_first_is_reordered():
    title, author = titles._split_livedub_title_author(
        "R.C. Sproul: True Seeker-Sensitive Churches", "", "", "BibleQ&A"
    )
    assert title == "True Seeker-Sensitive Churches"
    assert author == "Р. Ч. Спроул"
    assert (
        titles._format_livedub_title_line("Р. Ч. Спроул - True Seeker-Sensitive Churches", "")
        == "True Seeker-Sensitive Churches - Р. Ч. Спроул"
    )


def test_livedub_send_video_caption_includes_title_and_html_parse_mode():
    main_src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    title_src = Path("pipelines/livedub_title.py").read_text(encoding="utf-8")
    assert "async def _translate_livedub_title_for_caption" in title_src
    assert "_translate_livedub_title_for_caption_impl" in main_src
    assert "_gemini_clients=GEMINI_CLIENTS" in main_src
    assert "_livedub_title_html" in main_src
    assert "<b>{_livedub_title_html}</b>" in main_src
    # Both fresh delivery and the file_id cache keep HTML captions in main.
    helper = main_src[
        main_src.index("async def _send_livedub_result") : main_src.index("performer, title = parse_title")
    ]
    assert helper.count('parse_mode="HTML"') >= 2
    assert "🎬 Живые голоса Яндекса" in helper
    assert "if not (_livedub_cached_file_id or live_dub_task):" in helper


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 256), ("", 256), ("   ", 256), ("32", 32), ("many", 256)],
)
def test_cache_max_entries_preserves_environment_fallback(raw, expected):
    assert titles._cache_max_entries(raw) == expected


def test_translation_cache_hit_skips_fallback_and_gemini(monkeypatch):
    cache_key = ("Full", "Parsed", "Channel", "", "")
    titles._LIVEDUB_TITLE_CACHE[cache_key] = ("Готовое Название", "Автор")

    def forbidden_fallback(*args):
        raise AssertionError("fallback must not run after a cache hit")

    monkeypatch.setattr(titles, "_fallback_livedub_ru_title", forbidden_fallback)
    assert _translate("Full", "Parsed", "Performer", "Channel") == (
        "Готовое Название",
        "Автор",
    )


def test_analysis_title_and_author_independently_use_metadata_fallback(monkeypatch):
    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [])
    fallback_title, fallback_author = titles._fallback_livedub_ru_title(
        "True Faith - Paul Washer",
        "",
        "",
        "",
    )
    assert _translate(
        "True Faith - Paul Washer",
        "",
        "",
        "",
        analysis_title="Истинная Вера",
    ) == ("Истинная Вера", fallback_author)
    assert _translate(
        "True Faith - Paul Washer",
        "",
        "",
        "Other Channel",
        analysis_author="Другой Автор",
    ) == (fallback_title, "Другой Автор")


def test_no_gemini_clients_uses_metadata_without_light_card(monkeypatch):
    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "1")

    async def forbidden_card(*args, **kwargs):
        raise AssertionError("light card must not run without clients")

    monkeypatch.setattr(livedub_info, "build_livedub_info_card", forbidden_card)
    expected = titles._fallback_livedub_ru_title("True Faith - Paul Washer", "", "", "")
    assert _translate("True Faith - Paul Washer", "", "", "") == expected


def test_non_russian_light_title_uses_shared_main_gemini_router(monkeypatch):
    calls = _install_main_gemini(
        monkeypatch,
        '```json\n{"title_ru":"Вера","author_ru":"Paul Washer"}\n```',
        {"youtube_title": "Still English"},
    )
    result = _translate("Assurance - Paul Washer", "", "", "HeartCry")

    assert result == ("Вера", "Пол Вошер")
    assert calls["models"] == [titles.GEMINI_MODEL]
    assert calls["config"] == [
        {
            "temperature": 0.1,
            "max_output_tokens": 512,
            "thinking_level": "minimal",
            "response_mime_type": "application/json",
        }
    ]
    assert len(calls["sdk"]) == 1
    sdk_call = calls["sdk"][0]
    assert sdk_call["model"] == titles.GEMINI_MODEL
    assert "title: Assurance - Paul Washer" in sdk_call["contents"]
    assert sdk_call["config"] == calls["config"][0]


def test_empty_main_gemini_fields_fall_back_to_metadata(monkeypatch):
    _install_main_gemini(
        monkeypatch,
        '{"title_ru":"","author_ru":""}',
        {},
    )
    expected = titles._fallback_livedub_ru_title("Assurance - Paul Washer", "", "", "")
    assert _translate("Assurance - Paul Washer", "", "", "") == expected


def test_light_and_main_gemini_failures_preserve_metadata_fallback(monkeypatch):
    monkeypatch.setattr(titles, "GEMINI_CLIENTS", [object()])
    monkeypatch.setenv("LIVEDUB_TITLE_TRANSLATE", "1")

    async def failed_card(*args, **kwargs):
        raise RuntimeError("light unavailable")

    async def failed_router(*args, **kwargs):
        raise RuntimeError("main unavailable")

    monkeypatch.setattr(livedub_info, "build_livedub_info_card", failed_card)
    monkeypatch.setattr(core_globals, "make_text_config_smart", lambda **kwargs: kwargs)
    monkeypatch.setattr(core_globals, "gemini_generate", failed_router)
    expected = titles._fallback_livedub_ru_title("Assurance - Paul Washer", "", "", "")
    assert _translate("Assurance - Paul Washer", "", "", "") == expected


def test_split_and_format_defensive_edges(monkeypatch):
    assert titles._split_title_author_line("") == ("", "")
    assert titles._split_title_author_line("John Doe & Jane Roe - true faith") == (
        "true faith",
        "John Doe & Jane Roe",
    )
    assert titles._split_title_author_line("true faith - John Doe & Jane Roe") == (
        "true faith",
        "John Doe & Jane Roe",
    )
    assert titles._format_livedub_title_line("faith: journey", "") == "faith: journey"
    assert titles._format_livedub_title_line("", "") == "Переведённое видео"
    assert titles._format_livedub_title_line("Пол Вошер О Вере", "Пол Вошер") == ("Пол Вошер о Вере")

    # Keep the empty-side guard honest even if metadata cleaning changes later.
    monkeypatch.setattr(titles, "_clean_livedub_meta_text", lambda text: str(text or ""))
    assert titles._split_title_author_line(" - True") == (" - True", "")


def test_split_livedub_metadata_fallbacks_and_channel_mapping():
    assert titles._split_livedub_title_author("", "", "Solo Speaker", "") == (
        "",
        "Solo Speaker",
    )
    assert titles._split_livedub_title_author("Complete Title", "Paul Washer", "", "") == (
        "Complete Title",
        "",
    )
    _title, author = titles._fallback_livedub_ru_title("Talk - Grace to You", "", "", "")
    assert author == "Джон МакАртур"
