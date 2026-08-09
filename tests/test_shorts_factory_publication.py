from __future__ import annotations

import asyncio
from types import SimpleNamespace

import services.shorts_factory_publication as publication


def test_canonical_public_hashtags_follow_repository_rule():
    assert publication.canonical_public_hashtags(
        ["евангелие", "#бытие", "искупление", "спроул"]
    ) == ["#Евангелие", "#Бытие", "#Искупление", "#Спроул"]


def test_light_model_filter_never_spends_heavy_or_legacy_quota():
    assert publication._light_only_models(
        [
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
            "gemini-3.1-pro-preview",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
        ]
    ) == ["gemini-3.5-flash-lite", "gemini-3.5-flash"]


def test_caption_wrapper_inserts_human_paragraph_before_links_and_normalizes_tags():
    def original(*, candidate, **_kwargs):
        tags = " ".join(candidate["hashtags"])
        return f"Заголовок - Автор\n\nПолное видео:\nYouTube\n\n{tags}"

    wrapped = publication.wrap_factory_caption_builder(original)
    caption = wrapped(
        candidate={
            "hashtags": ["евангелие", "бытие", "искупление", "спроул"],
            "publication_description": (
                "Спроул показывает, почему первая попытка человека скрыть свой "
                "стыд не решает проблему греха и почему покрытие даёт Сам Бог."
            ),
        }
    )

    parts = caption.split("\n\n")
    assert parts[0] == "Заголовок - Автор"
    assert parts[1].startswith("Спроул показывает")
    assert parts[2].startswith("Полное видео")
    assert parts[3] == "#Евангелие #Бытие #Искупление #Спроул"
    assert "#евангелие" not in caption


def test_enrichment_fails_open_when_light_description_is_unavailable(monkeypatch):
    async def no_description(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(publication, "_generate_descriptions", no_description)
    source = [{"title": "Тема", "hashtags": ["евангелие", "бытие"]}]
    result = asyncio.run(publication.enrich_factory_candidates(source))

    assert result[0]["hashtags"] == ["#Евангелие", "#Бытие"]
    assert "publication_description" not in result[0]
    assert source[0]["hashtags"] == ["евангелие", "бытие"]


def test_description_cleaner_rejects_ai_or_provider_meta_text():
    assert publication._clean_description(
        "В этом видео нейросеть кратко объясняет содержание фрагмента и перевод Яндекса."
    ) == ""
    assert publication._clean_description(
        "Человек пытается прикрыть последствия греха своими средствами, но окончательное покрытие приходит от Бога."
    ).endswith(".")
