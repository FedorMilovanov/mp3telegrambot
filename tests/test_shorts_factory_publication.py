from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import services.shorts_factory_publication as publication


def test_canonical_public_hashtags_follow_repository_rule():
    assert publication.canonical_public_hashtags(
        ["евангелие", "#бытие", "искупление", "спроул"]
    ) == ["#Евангелие", "#Бытие", "#Искупление", "#Спроул"]


def test_light_model_route_is_exact_cheapest_first_and_not_env_driven(monkeypatch):
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv(
        "GEMINI_LIGHT_FALLBACK_MODELS",
        "gemini-3.5-pro,gemini-3.1-pro-preview,gemini-2.5-flash",
    )
    assert publication._light_models() == [
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    ]
    assert publication.FACTORY_PUBLICATION_LIGHT_MODELS == (
        "gemini-3.5-flash-lite",
        "gemini-3.5-flash",
    )


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


def test_caption_wrapper_html_escapes_generated_description():
    def original(*, candidate, **_kwargs):
        return "Заголовок - Автор\n\nПолное видео:\nYouTube"

    wrapped = publication.wrap_factory_caption_builder(original)
    caption = wrapped(
        candidate={
            "hashtags": [],
            "publication_description": (
                "Бог и человек не находятся в отношении 1 & 1: искупление "
                "исходит от Бога и не сводится к человеческой попытке скрыть вину."
            ),
        }
    )
    assert "1 &amp; 1" in caption
    assert "1 & 1" not in caption


def test_enrichment_fails_open_when_light_description_is_unavailable(monkeypatch):
    async def no_description(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(publication, "_generate_descriptions", no_description)
    source = [{"title": "Тема", "hashtags": ["евангелие", "бытие"]}]
    result = asyncio.run(publication.enrich_factory_candidates(source))

    assert result[0]["hashtags"] == ["#Евангелие", "#Бытие"]
    assert "publication_description" not in result[0]
    assert source[0]["hashtags"] == ["евангелие", "бытие"]


def test_description_generation_uses_only_lite_then_flash(monkeypatch):
    import core.globals as globals_module

    calls: list[str] = []
    configs: list[dict] = []

    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            del contents, config
            calls.append(model)
            if model == "gemini-3.5-flash-lite":
                raise RuntimeError("simulated light quota")
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "items": [
                            {
                                "index": 0,
                                "description": (
                                    "Искупление начинается с Божьего действия: "
                                    "человеческая попытка скрыть вину не решает саму проблему греха."
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    fake_client = SimpleNamespace(aio=SimpleNamespace(models=FakeModels()))

    def fake_config(**kwargs):
        configs.append(dict(kwargs))
        return kwargs

    monkeypatch.setattr(globals_module, "GEMINI_CLIENTS", [fake_client])
    monkeypatch.setattr(globals_module, "make_text_config_smart", fake_config)
    monkeypatch.setattr(publication, "_timeout", lambda: 5.0)

    result = asyncio.run(
        publication._generate_descriptions(
            [
                {
                    "title": "Первое Искупительное Действие Бога",
                    "hook": "Что делает Бог после грехопадения?",
                    "reason": "Божье покрытие противопоставлено человеческой попытке скрыться.",
                }
            ],
            args=(),
            kwargs={},
            kind="short",
        )
    )

    assert calls == ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
    assert [cfg["model_name"] for cfg in configs] == calls
    assert all(cfg["thinking_level"] == "minimal" for cfg in configs)
    assert not any("3.6" in model or "3.1" in model or "2.5" in model for model in calls)
    assert result[0].startswith("Искупление начинается")


def test_description_cleaner_rejects_ai_or_provider_meta_text():
    assert publication._clean_description(
        "В этом видео нейросеть кратко объясняет содержание фрагмента и перевод Яндекса."
    ) == ""
    assert publication._clean_description(
        "Человек пытается прикрыть последствия греха своими средствами, но окончательное покрытие приходит от Бога."
    ).endswith(".")
