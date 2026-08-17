import asyncio
import importlib.util
from pathlib import Path

from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES


def _load(name: str, filename: str):
    path = Path(__file__).parents[1] / "services" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


publication = _load("livedub_publication_core_test", "livedub_publication_core.py")
qa = _load("livedub_qa_hardening_test", "livedub_qa_hardening.py")


def test_russian_title_case_has_broad_lowercase_service_words():
    assert publication.russian_title_case(
        "борьба со страхом через веру и между испытаниями"
    ) == "Борьба со Страхом через Веру и между Испытаниями"


def test_reported_title_override_and_author_are_canonical():
    title, author = publication.split_title_author(
        "The Battle Against Sexual Immorality & Pornography - Tim Conway"
    )
    assert publication.canonical_title(title) == (
        "Борьба с Сексуальной Безнравственностью и Порнографией"
    )
    assert author == "Тим Конвей"


def test_all_caps_title_is_normalized_but_real_acronyms_survive():
    assert publication.russian_title_case(
        "БОРЬБА С ГРЕХОМ И ИИ"
    ) == "Борьба с Грехом и ИИ"


def test_audio_filename_is_russian_safe_and_bounded():
    name = publication.safe_audio_filename('Борьба: с Грехом / "Практика"?')
    assert name.endswith(".mp3")
    assert not any(char in name for char in '<>:"/\\|?*')
    assert len(name) <= 124


def test_cached_file_id_never_receives_synthetic_filename():
    src = (Path(__file__).parents[1] / "services/livedub_delivery_coordinator.py").read_text(
        encoding="utf-8"
    )
    start = src.index("async def deliver_cached_companions")
    end = src.index("@dataclass", start)
    cached = src[start:end]
    assert 'audio=meta["audio_file_id"]' in cached
    assert "filename=" not in cached


def test_mp3_metadata_is_bounded_without_tiny_fragment():
    text = "Очень Длинное Название Проповеди о Борьбе с Искушением и Сохранении Чистоты Сердца"
    result = publication.metadata_text(text)
    assert len(result) <= 64
    assert len(result) >= 32
    assert not result.endswith(" ")


def test_publication_models_are_exact_37_even_with_stale_utility_env(monkeypatch):
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv(
        "LIVEDUB_PUBLICATION_FALLBACK_MODELS",
        "gemini-3.5-flash,gemini-3.5-flash-lite",
    )
    monkeypatch.setenv("LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK", "1")
    assert publication.publication_models() == ["gemini-3.7-flash"]


def test_publication_strong_fallback_flag_cannot_reenable_35_semantics(monkeypatch):
    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_PUBLICATION_FALLBACK_MODELS", "gemini-3.5-flash")
    monkeypatch.setenv("LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK", "1")
    assert publication.publication_models() == ["gemini-3.7-flash"]


def test_bounded_lru_publication_cache(monkeypatch):
    monkeypatch.setenv("LIVEDUB_PUBLICATION_CACHE_MAX", "16")
    publication._CACHE.clear()
    for index in range(25):
        publication.cache_put({"title": str(index)}, f"line:{index}")
    assert len(publication._CACHE) == 16
    assert "line:0" not in publication._CACHE
    assert "line:24" in publication._CACHE


def _issue(time: str, heard: str, problem: str = "неверная цитата", severity: str = "major"):
    return {
        "time": time,
        "heard": heard,
        "problem": problem,
        "should_be": "правильная формулировка",
        "severity": severity,
    }


def test_generic_problem_words_cannot_confirm_unrelated_heard_phrase():
    first = _issue("18:32", "если твой правый глаз соблазняет тебя")
    unrelated = _issue("18:34", "совсем другая мысль о прощении брата")
    assert not qa.issues_match_strict(first, unrelated)


def test_confirmation_is_one_to_one():
    first = _issue("18:32", "правый глаз соблазняет тебя")
    duplicate = _issue("18:36", "правый глаз соблазняет тебя")
    validation = _issue("18:34", "правый глаз соблазняет тебя")
    result = qa.confirmed_result_one_to_one(
        {"score": 70, "issues": [first, duplicate]},
        {"issues": [validation]},
    )
    assert len(result["issues"]) == 1
    assert result["_qa_unconfirmed_dropped"] == 1


def test_empty_focused_result_rejects_candidates_and_removes_score():
    result = qa.confirmed_result_one_to_one(
        {"score": 65, "issues": [_issue("01:00", "ошибочная фраза")]},
        {"issues": []},
    )
    assert result["issues"] == []
    assert "score" not in result
    assert "не подтвердила" in result["verdict"]


def test_inflight_requests_share_one_task(monkeypatch):
    publication._CACHE.clear()
    publication._INFLIGHT.clear()
    calls = 0

    async def fake_build(source_line, source_url):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {
            "title": "Русское Название",
            "author": "Тим Конвей",
            "description": "Описание.",
            "source_url": source_url,
            "model": "fake-quality",
        }

    monkeypatch.setattr(publication, "_build_uncached", fake_build)

    async def run():
        return await asyncio.gather(
            publication.build_publication_card("Same title", "https://youtu.be/x"),
            publication.build_publication_card("Same title", "https://youtu.be/x"),
        )

    first, second = asyncio.run(run())
    assert calls == 1
    assert first == second


def test_same_title_at_two_urls_does_not_reuse_wrong_source(monkeypatch):
    publication._CACHE.clear()
    publication._INFLIGHT.clear()
    calls = 0

    async def fake_build(source_line, source_url):
        nonlocal calls
        calls += 1
        return {
            "title": "Одинаковое Название",
            "author": "Автор",
            "description": source_url,
            "source_url": source_url,
            "model": "fake-quality",
        }

    monkeypatch.setattr(publication, "_build_uncached", fake_build)

    async def run():
        first = await publication.build_publication_card(
            "Same", "https://youtu.be/one"
        )
        second = await publication.build_publication_card(
            "Same", "https://youtu.be/two"
        )
        return first, second

    first, second = asyncio.run(run())
    assert calls == 2
    assert first["source_url"].endswith("one")
    assert second["source_url"].endswith("two")


def test_manifest_uses_source_owned_livedub_contracts_only():
    feature_ids = {feature.feature_id for feature in DEFAULT_RUNTIME_FEATURES}
    assert "livedub-qa-contract" in feature_ids
    assert "livedub-delivery-contract" in feature_ids
    for retired in (
        "livedub-deep-audit",
        "livedub-audio-dedupe",
        "project-runtime-hardening",
    ):
        assert retired not in feature_ids
