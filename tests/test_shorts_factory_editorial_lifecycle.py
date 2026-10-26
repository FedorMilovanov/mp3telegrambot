from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pipelines.translation_editorial_runner as runner
import services.shorts_factory_source as factory_source


def test_editorial_runner_has_no_audio_ordering_marker_or_job_context():
    source = inspect.getsource(runner)
    assert "mark_factory_analysis_audio_skipped" not in source
    assert "JOB_STATE" not in source
    assert "ContextVar" not in source
    assert "translation_editorial_pending" not in source


def test_editorial_runner_passes_known_duration_into_source_owned_translation():
    source = inspect.getsource(runner.process_translation_editorial_only)
    assert 'prepare_factory_translation_video(url, workdir, duration, "en")' in source
    assert "factory_preflight_issues(" in source
    assert "enforce_factory_translation_preflight()" in source


def test_translation_source_owns_video_capacity_proof_before_download():
    source = inspect.getsource(factory_source.prepare_factory_translation_video)
    ensure_pos = source.index("ensure_factory_video_space(")
    original_task_pos = source.index("download_factory_video_source(")
    assert ensure_pos < original_task_pos
    assert "duration_seconds=float(duration)" in source
    assert "expected_duration=float(duration)" in source


def test_factory_pipeline_builds_editorial_pack_directly_without_handoff_bridge():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    assert "prepare_factory_editorial_review(" in source
    assert "send_factory_editorial_files(" in source
    assert "shorts_factory_editorial_bridge" not in source
    assert "editorial_source" not in source


def test_translation_editorial_runner_is_the_only_standalone_owner():
    dispatcher = Path("pipelines/video_dispatch.py").read_text(encoding="utf-8")
    retired_bridge = Path("services/shorts_factory_overload_editorial_polish.py")
    assert "pipelines.translation_editorial_runner" in dispatcher
    assert not retired_bridge.exists()
    assert "shorts_factory_editorial_bridge" not in dispatcher


def test_pipeline_editorial_runner_executes_success_and_cleans_workdir(monkeypatch, tmp_path):
    import pipelines.shorts_factory as factory_pipeline
    import services.media_delivery_probe as delivery_probe
    import services.shorts_video_impl as shorts_video_impl

    workdir = tmp_path / "editorial-work"
    workdir.mkdir()
    translated = tmp_path / "translated.mp4"
    pack = tmp_path / "pack.zip"
    review = tmp_path / "review.zip"
    markdown = tmp_path / "review.md"
    calls = {}

    async def load_info(_url):
        return {
            "id": "video",
            "duration": 100,
            "language": "en",
            "title": "Title",
            "channel": "Channel",
        }

    async def prepare_translation(url, actual_workdir, duration, language):
        calls["translation"] = (url, Path(actual_workdir), duration, language)
        return translated

    async def prepare_review(**kwargs):
        calls["review"] = kwargs
        return pack, review, markdown

    async def heartbeat(awaitable, **kwargs):
        calls.setdefault("heartbeats", []).append(kwargs["label"])
        return await awaitable

    async def probe(_path):
        return SimpleNamespace(duration=100.0)

    async def send_files(update, **kwargs):
        calls["send"] = (update, kwargs)

    async def safe_status(status, text):
        calls["status"] = (status, text)

    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda prefix: str(workdir))
    monkeypatch.setattr(factory_pipeline, "_load_video_info", load_info)
    monkeypatch.setattr(factory_pipeline, "_media_id", lambda _info, _url: "media")
    monkeypatch.setattr(shorts_video_impl, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _path: SimpleNamespace(free=20 * 1024**3))
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner, "enforce_factory_translation_preflight", lambda: None)
    monkeypatch.setattr(runner, "parse_title", lambda *_args: ("Performer", "Title"))
    monkeypatch.setattr(runner, "prepare_factory_translation_video", prepare_translation)
    monkeypatch.setattr(runner, "prepare_factory_editorial_review", prepare_review)
    monkeypatch.setattr(runner, "await_with_heartbeat", heartbeat)
    monkeypatch.setattr(runner, "send_factory_editorial_files", send_files)
    monkeypatch.setattr(runner, "safe_status", safe_status)
    monkeypatch.setattr(delivery_probe, "probe_media_async", probe)
    monkeypatch.setattr(delivery_probe, "media_probe_is_deliverable", lambda _probe: True)

    update = SimpleNamespace()
    status = object()
    result = asyncio.run(runner.process_translation_editorial_only("url", update, status_msg=status))

    assert result is True
    assert calls["translation"] == ("url", workdir, 100, "en")
    assert calls["review"] == {
        "url": "url",
        "media_id": "media",
        "title": "Title",
        "performer": "Performer",
        "duration": 100.0,
        "source_language": "en",
        "translated_video_path": translated,
        "shorts_candidates": [],
        "long_candidates": [],
        "ai_data": None,
    }
    assert calls["send"] == (
        update,
        {"pack_path": pack, "review_path": review, "markdown_path": markdown},
    )
    assert calls["status"] == (status, "✅ РЕДАКТОР ПЕРЕВОДА: ZIP готов. Пришлите его в ChatGPT.")
    assert len(calls["heartbeats"]) == 2
    assert not workdir.exists()
