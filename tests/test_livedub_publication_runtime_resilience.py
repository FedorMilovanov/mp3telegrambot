import asyncio
import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).parents[1] / "services/livedub_publication_core.py"
    spec = importlib.util.spec_from_file_location("livedub_publication_resilience_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


publication = _load_module()


def test_stale_inflight_from_old_event_loop_is_not_awaited(monkeypatch):
    publication._CACHE.clear()
    publication._INFLIGHT.clear()
    old_loop = asyncio.new_event_loop()
    stale = old_loop.create_future()
    publication._INFLIGHT["url:https://youtu.be/restart"] = stale
    calls = 0

    async def fake_build(source_line, source_url):
        nonlocal calls
        calls += 1
        return {
            "title": "Название После Перезапуска",
            "author": "Автор",
            "description": "Описание.",
            "source_url": source_url,
            "model": "fake-quality",
        }

    monkeypatch.setattr(publication, "_build_uncached", fake_build)

    async def run():
        return await publication.build_publication_card(
            "Title", "https://youtu.be/restart"
        )

    try:
        result = asyncio.run(run())
    finally:
        old_loop.close()
    assert calls == 1
    assert result["title"] == "Название После Перезапуска"


def test_missing_quality_config_falls_back_without_unconfigured_ai_call():
    src = (Path(__file__).parents[1] / "services/livedub_publication_core.py").read_text(
        encoding="utf-8"
    )
    assert "if config is None:" in src
    assert "using deterministic fallback instead" in src
    assert "config=config" in src
    assert "_quality_config" in src
    assert 'thinking_level="high"' in src
    assert "GEMINI_LIGHT_MODEL" not in src
    assert "temperature=" not in src
