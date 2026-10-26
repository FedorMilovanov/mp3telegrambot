#!/usr/bin/env python3
"""Стадия анализа: порядок шагов, деградация и подмена заголовка.

Проверяется поведение, а не строки: фейковые зависимости фиксируют порядок
вызовов — «ссылки до конспекта», «прогресс до анализа» — потому что именно
порядок был неявным контрактом внутри `process_single_video`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from pipelines.analysis_stage import (
    ANALYZE_STEP,
    SYNOPSIS_STEP,
    AnalysisDeps,
    AnalysisRequest,
    apply_safe_title,
    build_degraded_data,
    run_analysis,
)


class Recorder:
    """Фейковые зависимости, записывающие порядок вызовов."""

    def __init__(
        self,
        *,
        ai_data=None,
        client=None,
        audio_part=None,
        links=None,
        links_exc=None,
        synopsis_result=("https://tg/ok", "- пункт"),
        synopsis_enabled=True,
        transcript=None,
    ):
        self.calls: list[tuple] = []
        self._ai_data = ai_data
        self._client = client
        self._audio_part = audio_part
        self._links = links
        self._links_exc = links_exc
        self._synopsis_result = synopsis_result
        self._synopsis_enabled = synopsis_enabled
        self.progress_events: list[tuple[int, dict | None]] = []

    async def analyze(self, *args, **kwargs):
        self.calls.append(("analyze", args, kwargs))
        return self._ai_data, self._client, self._audio_part

    async def create_synopsis(self, *args, **kwargs):
        self.calls.append(("synopsis", args, kwargs))
        return self._synopsis_result

    async def find_links(self, *args, **kwargs):
        self.calls.append(("links", args, kwargs))
        if self._links_exc is not None:
            raise self._links_exc
        return self._links

    async def get_setting(self, key):
        self.calls.append(("setting", key))
        return self._synopsis_enabled

    async def progress(self, status_msg, step, info=None, prefix=""):
        self.calls.append(("progress", step))
        self.progress_events.append((step, info))

    @staticmethod
    def build_search_title(ai_data, fallback_title):
        return f"search:{fallback_title}"

    def deps(self) -> AnalysisDeps:
        return AnalysisDeps(
            analyze=self.analyze,
            create_synopsis=self.create_synopsis,
            find_links=self.find_links,
            get_setting=self.get_setting,
            progress=self.progress,
            build_search_title=self.build_search_title,
        )

    def names(self) -> list[str]:
        return [c[0] if c[0] != "progress" else f"progress{c[1]}" for c in self.calls]


@pytest.fixture()
def mp3(tmp_path: Path) -> Path:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    return path


def make_request(mp3: Path, tmp_path: Path, **overrides) -> AnalysisRequest:
    base = dict(
        mp3_path=mp3,
        title="Проповедь",
        performer="Автор",
        duration=125,
        url="https://youtu.be/abc",
        full_title="Полное название ролика",
        channel_name="Канал",
        media_id="vid1",
        download_dir=tmp_path,
        bitrate="192",
    )
    base.update(overrides)
    return AnalysisRequest(**base)


AI = {"real_title": "Тема", "real_author": "Автор", "main_topic": "молитва и пост"}


@pytest.mark.asyncio
async def test_disabled_stage_does_nothing(mp3: Path, tmp_path: Path) -> None:
    rec = Recorder(ai_data=AI)
    outcome = await run_analysis(make_request(mp3, tmp_path), rec.deps(), enabled=False)

    assert outcome.ai_data is None
    assert outcome.caption_data is None
    assert rec.calls == []


@pytest.mark.asyncio
async def test_order_progress_then_analyze_then_links_then_synopsis(mp3: Path, tmp_path: Path) -> None:
    rec = Recorder(ai_data=dict(AI), links={"rutube": "https://rt/v", "vk": "https://vk/v"})
    outcome = await run_analysis(make_request(mp3, tmp_path), rec.deps())

    assert rec.names() == [
        f"progress{ANALYZE_STEP}",
        "analyze",
        f"progress{SYNOPSIS_STEP}",
        "links",
        "setting",
        "synopsis",
    ]
    # Ссылки должны быть известны конспекту: таймкоды платформ попадают на
    # страницу с первой публикации, а не при последующей правке.
    synopsis_kwargs = next(c[2] for c in rec.calls if c[0] == "synopsis")
    assert synopsis_kwargs["rutube_url"] == "https://rt/v"
    assert synopsis_kwargs["vk_url"] == "https://vk/v"
    assert outcome.telegraph_url == "https://tg/ok"
    assert outcome.synopsis_outline == "- пункт"


@pytest.mark.asyncio
async def test_analyze_receives_exact_arguments(mp3: Path, tmp_path: Path) -> None:
    rec = Recorder(ai_data=dict(AI), client="cli-7", audio_part="part-3")
    outcome = await run_analysis(make_request(mp3, tmp_path), rec.deps())

    (_, args, _) = next(c for c in rec.calls if c[0] == "analyze")
    assert args == (mp3, "Проповедь", "Автор", 125, None, "")
    assert outcome.used_client == "cli-7"
    assert outcome.used_audio_part == "part-3"


@pytest.mark.asyncio
async def test_progress_info_describes_the_recording(mp3: Path, tmp_path: Path) -> None:
    rec = Recorder(ai_data=dict(AI), links={})
    await run_analysis(make_request(mp3, tmp_path), rec.deps())

    step, info = rec.progress_events[0]
    assert step == ANALYZE_STEP
    assert info == {
        "title": "📝 Проповедь",
        "author": "👤 Автор  •  2:05",
        "size": "🎵 2.0 МБ  •  192 kbps",
    }


@pytest.mark.asyncio
async def test_synopsis_disabled_leaves_urls_empty(mp3: Path, tmp_path: Path) -> None:
    rec = Recorder(ai_data=dict(AI), links={"rutube": "https://rt/v"}, synopsis_enabled=False)
    outcome = await run_analysis(make_request(mp3, tmp_path), rec.deps())

    assert "synopsis" not in rec.names()
    assert outcome.telegraph_url is None
    assert outcome.synopsis_outline is None
    assert outcome.caption_data == AI


@pytest.mark.asyncio
async def test_links_failure_does_not_block_publication(
    mp3: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rec = Recorder(ai_data=dict(AI), links_exc=RuntimeError("поиск упал"))
    with caplog.at_level(logging.WARNING):
        outcome = await run_analysis(make_request(mp3, tmp_path), rec.deps())

    assert "early alt-links error: поиск упал" in caplog.text
    assert outcome.rutube_url == ""
    assert outcome.vk_url == ""
    assert outcome.early_links is None
    synopsis_kwargs = next(c[2] for c in rec.calls if c[0] == "synopsis")
    assert synopsis_kwargs["rutube_url"] == ""


@pytest.mark.asyncio
async def test_no_analysis_goes_to_degraded_mode_not_to_pages(
    mp3: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("DEGRADED_TRANSCRIPT_TIMESTAMPS", "0")
    rec = Recorder(ai_data=None)
    with caplog.at_level(logging.WARNING):
        outcome = await run_analysis(make_request(mp3, tmp_path), rec.deps())

    assert outcome.ai_data is None
    assert outcome.degraded_caption_data is not None
    assert outcome.degraded_caption_data["real_title"] == "Проповедь"
    assert "_ai_unavailable_warning" in outcome.degraded_caption_data
    assert outcome.caption_data is outcome.degraded_caption_data
    assert "honest degraded mode" in caplog.text
    # Публикация AI-страниц без анализа — это галлюцинации, поэтому конспекта нет.
    assert "synopsis" not in rec.names()
    assert "setting" not in rec.names()
    assert "links" not in rec.names()


@pytest.mark.asyncio
async def test_degraded_timestamps_are_attached_when_available(
    mp3: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEGRADED_TRANSCRIPT_TIMESTAMPS", raising=False)
    req = make_request(mp3, tmp_path)

    async def fake_fetch(url, work_dir, **kwargs):
        return "00:00 начало\n01:30 середина"

    data = await build_degraded_data(req)
    assert "_ai_unavailable_warning" in data

    from pipelines import degraded_mode as dm

    data2 = await dm.attach_transcript_timestamps(
        data,
        url=req.url,
        media_id=req.media_id,
        download_dir=req.download_dir,
        duration=req.duration,
        fetch=fake_fetch,
        to_timestamps=lambda text, **kw: "#таймкоды\n" + text,
    )
    assert "00:00 начало" in data2["timestamps"]


def test_safe_title_replaces_mismatched_title() -> None:
    ai = {
        "real_title": "Совершенно другая тема",
        "title_topic_warning": True,
        "main_topic": "молитва и пост",
        "timestamps": "00:00 молитва и пост",
    }
    new, replaced = apply_safe_title(ai, "Молитва и пост")

    assert replaced is True
    assert new["real_title"] == "Молитва и пост"
    assert ai["real_title"] == "Совершенно другая тема"  # исходный не мутирован


def test_safe_title_keeps_good_title() -> None:
    ai = {"real_title": "Молитва и пост", "title_topic_warning": True, "main_topic": "молитва и пост"}
    new, replaced = apply_safe_title(ai, "Молитва и пост")
    assert replaced is False
    assert new is ai


def test_safe_title_ignored_without_warning() -> None:
    ai = {"real_title": "Что угодно"}
    new, replaced = apply_safe_title(ai, "Другое")
    assert (new, replaced) == (ai, False)


@pytest.mark.asyncio
async def test_safe_title_applied_before_synopsis(mp3: Path, tmp_path: Path) -> None:
    ai = {
        "real_title": "Совершенно другая тема",
        "title_topic_warning": True,
        "main_topic": "молитва и пост",
        "timestamps": "00:00 молитва и пост",
    }
    rec = Recorder(ai_data=ai, links={})
    outcome = await run_analysis(make_request(mp3, tmp_path, full_title="Молитва и пост"), rec.deps())

    assert outcome.safe_title_replaced is True
    assert outcome.ai_data["real_title"] == "Молитва и пост"
    synopsis_ai = next(c[2] for c in rec.calls if c[0] == "synopsis")["ai_data"]
    assert synopsis_ai["real_title"] == "Молитва и пост"


def test_production_deps_wire_real_boundaries() -> None:
    """Боевые зависимости — те же функции, что использовал main_pipeline.

    Тест ловит молчаливую подмену: если `production_deps` начнёт возвращать
    заглушку, стадия перестанет анализировать, но тесты на фейках этого не
    заметят.
    """
    from core.database import asettings_get
    from core.progress import set_progress
    from pipelines.analysis_stage import production_deps
    from services.gemini_analyze import gemini_analyze_audio
    from services.search import _build_search_title, find_alternative_links
    from services.telegraph import create_telegraph_synopsis

    deps = production_deps()
    assert deps.analyze is gemini_analyze_audio
    assert deps.create_synopsis is create_telegraph_synopsis
    assert deps.find_links is find_alternative_links
    assert deps.get_setting is asettings_get
    assert deps.progress is set_progress
    assert deps.build_search_title is _build_search_title
