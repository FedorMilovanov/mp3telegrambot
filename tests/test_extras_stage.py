#!/usr/bin/env python3
"""Post-delivery extras: порядок, отказы и LiveDub без реальных рендеров."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

import pipelines.extras_stage as extras_stage
from pipelines.extras_stage import (
    ExtrasDeps,
    ExtrasRequest,
    empty_candidates,
    run_extras,
    run_optional_stage,
)

URL = "https://youtu.be/video-id"
AI_DATA = {"real_title": "Название", "real_author": "Автор"}
CANDIDATES = {
    "montage_candidates": [{"id": "montage"}],
    "highlights_candidates": [{"id": "highlights"}],
}
ALL_FEATURES = {
    "shorts": True,
    "clips": True,
    "shorts_montage": True,
    "shorts_highlights": True,
}


class _Harness:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.settings_calls: list[str] = []
        self.errors: set[str] = set()
        self.setting_errors: set[str] = set()
        self.candidate_result = CANDIDATES

    async def get_setting(self, key: str) -> Any:
        self.settings_calls.append(key)
        if key in self.setting_errors:
            raise RuntimeError(f"setting {key} failed")
        return self.settings.get(key, False)

    async def _record(self, name: str, kwargs: dict[str, Any]) -> Any:
        self.calls.append((name, kwargs))
        if name in self.errors:
            raise RuntimeError(f"{name} failed")
        if name == "candidates":
            return self.candidate_result
        return name

    async def shorts(self, **kwargs: Any) -> Any:
        return await self._record("shorts", kwargs)

    async def clips(self, **kwargs: Any) -> Any:
        return await self._record("clips", kwargs)

    async def candidates(self, **kwargs: Any) -> Any:
        return await self._record("candidates", kwargs)

    async def montage(self, **kwargs: Any) -> Any:
        return await self._record("montage", kwargs)

    async def highlights(self, **kwargs: Any) -> Any:
        return await self._record("highlights", kwargs)

    def deps(self) -> ExtrasDeps:
        return ExtrasDeps(
            get_setting=self.get_setting,
            run_shorts=self.shorts,
            run_clips=self.clips,
            create_candidates=self.candidates,
            run_montage=self.montage,
            run_highlights=self.highlights,
        )


async def _unexpected_send() -> bool:
    raise AssertionError("LiveDub не должен отправляться в этом сценарии")


def _request(tmp_path: Path, **overrides: Any) -> ExtrasRequest:
    values: dict[str, Any] = {
        "source_url": URL,
        "media_id": "video-id",
        "mp3_path": tmp_path / "video-id.mp3",
        "title": "Название",
        "performer": "Автор",
        "duration": 600,
        "ai_data": AI_DATA,
        "update": object(),
        "get_livedub_path": lambda: None,
        "send_livedub": _unexpected_send,
        "rutube_url": "https://rutube.example/video",
        "vk_url": "https://vk.example/video",
        "used_audio_part": "audio-part",
        "used_client": "client",
        "workdir": tmp_path / "livedub-work",
    }
    values.update(overrides)
    return ExtrasRequest(**values)


def _run(request: ExtrasRequest, harness: _Harness) -> None:
    asyncio.run(run_extras(request, harness.deps()))


def _kwargs(harness: _Harness, name: str) -> dict[str, Any]:
    return next(kwargs for called, kwargs in harness.calls if called == name)


def test_all_features_keep_order_and_share_one_candidates_call(tmp_path: Path) -> None:
    harness = _Harness(ALL_FEATURES)
    request = _request(tmp_path)

    _run(request, harness)

    assert [name for name, _kwargs in harness.calls] == [
        "shorts",
        "clips",
        "candidates",
        "montage",
        "highlights",
    ]
    assert harness.settings_calls == [
        "shorts",
        "clips",
        "shorts_montage",
        "shorts_highlights",
    ]
    common = {
        "url": URL,
        "media_id": "video-id",
        "mp3_path": request.mp3_path,
        "title": "Название",
        "performer": "Автор",
        "duration": 600,
        "ai_data": AI_DATA,
        "update": request.update,
    }
    assert _kwargs(harness, "shorts") == {
        **common,
        "existing_audio_part": "audio-part",
        "existing_client": "client",
        "rutube_url": request.rutube_url,
        "vk_url": request.vk_url,
        "workdir": request.workdir,
        "livedub_video_path": None,
    }
    assert _kwargs(harness, "clips") == {
        **common,
        "existing_audio_part": "audio-part",
        "existing_client": "client",
        "rutube_url": request.rutube_url,
        "vk_url": request.vk_url,
        "livedub_video_path": None,
    }
    assert _kwargs(harness, "candidates") == {
        "ai_data": AI_DATA,
        "title": "Название",
        "performer": "Автор",
        "duration": 600,
    }
    for name, candidate_key in (
        ("montage", "montage_candidates"),
        ("highlights", "highlights_candidates"),
    ):
        assert _kwargs(harness, name) == {
            **common,
            "rutube_url": request.rutube_url,
            "vk_url": request.vk_url,
            "prefetched_candidates": CANDIDATES[candidate_key],
            "livedub_video_path": None,
        }


def test_no_analysis_skips_renderers_but_keeps_settings_and_livedub_order(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness = _Harness(ALL_FEATURES)
    events: list[str] = []

    async def send_livedub() -> bool:
        events.append("send-livedub")
        return False

    request = _request(
        tmp_path,
        ai_data=None,
        user_mode="eng",
        live_dub_pending=True,
        send_livedub=send_livedub,
    )
    with caplog.at_level(logging.INFO):
        _run(request, harness)

    assert events == ["send-livedub"]
    assert harness.calls == []
    assert harness.settings_calls == list(ALL_FEATURES)
    assert "Shorts: skipped (feat=True, ai_data=no)" in caplog.text
    assert "Clips: skipped (feat=True, ai_data=no)" in caplog.text
    assert "Montage: skipped (feat=True)" in caplog.text
    assert "Highlights: skipped (feat=True)" in caplog.text


def test_all_features_disabled_do_not_request_candidates(tmp_path: Path) -> None:
    harness = _Harness({})

    _run(_request(tmp_path), harness)

    assert harness.calls == []
    assert harness.settings_calls == list(ALL_FEATURES)


@pytest.mark.parametrize(
    ("enabled", "expected"),
    [
        ("shorts_montage", ["candidates", "montage"]),
        ("shorts_highlights", ["candidates", "highlights"]),
    ],
)
def test_single_extra_still_uses_shared_candidates_request(
    tmp_path: Path,
    enabled: str,
    expected: list[str],
) -> None:
    harness = _Harness({enabled: True})

    _run(_request(tmp_path), harness)

    assert [name for name, _kwargs in harness.calls] == expected


@pytest.mark.parametrize(
    ("failed", "log_name"),
    [
        ("shorts", "process_and_send_shorts"),
        ("clips", "process_and_send_clips"),
        ("candidates", "create_extras_candidates"),
        ("montage", "process_and_send_montage"),
        ("highlights", "process_and_send_highlights"),
    ],
)
def test_optional_failure_does_not_cancel_other_extras(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    failed: str,
    log_name: str,
) -> None:
    harness = _Harness(ALL_FEATURES)
    harness.errors.add(failed)

    with caplog.at_level(logging.ERROR):
        _run(_request(tmp_path), harness)

    assert [name for name, _kwargs in harness.calls] == [
        "shorts",
        "clips",
        "candidates",
        "montage",
        "highlights",
    ]
    assert f"Optional stage {log_name} failed but pipeline continues: {failed} failed" in caplog.text
    if failed == "candidates":
        assert _kwargs(harness, "montage")["prefetched_candidates"] == []
        assert _kwargs(harness, "highlights")["prefetched_candidates"] == []


def test_setting_failure_remains_critical_and_stops_stage(tmp_path: Path) -> None:
    harness = _Harness(ALL_FEATURES)
    harness.setting_errors.add("clips")

    with pytest.raises(RuntimeError, match="setting clips failed"):
        _run(_request(tmp_path), harness)

    assert [name for name, _kwargs in harness.calls] == ["shorts"]
    assert harness.settings_calls == ["shorts", "clips"]


def test_eng_waits_for_livedub_and_passes_translated_file_to_shorts(tmp_path: Path) -> None:
    harness = _Harness({"shorts": True})
    translated = tmp_path / "translated.mp4"
    state: dict[str, Path | None] = {"path": None}
    events: list[str] = []

    def get_path() -> Path | None:
        events.append("get-path")
        return state["path"]

    async def send_livedub() -> bool:
        events.append("send-livedub")
        translated.write_bytes(b"translated")
        state["path"] = translated
        return True

    request = _request(
        tmp_path,
        user_mode="eng",
        live_dub_pending=True,
        get_livedub_path=get_path,
        send_livedub=send_livedub,
    )
    _run(request, harness)

    assert events == ["get-path", "send-livedub", "get-path"]
    assert _kwargs(harness, "shorts")["livedub_video_path"] == translated


def test_existing_livedub_file_avoids_duplicate_send(tmp_path: Path) -> None:
    harness = _Harness({"clips": True})
    translated = tmp_path / "translated.mp4"
    translated.write_bytes(b"translated")
    reads: list[Path | None] = []

    def get_path() -> Path | None:
        reads.append(translated)
        return translated

    request = _request(
        tmp_path,
        user_mode="eng",
        live_dub_pending=True,
        get_livedub_path=get_path,
    )
    _run(request, harness)

    assert reads == [translated]
    assert _kwargs(harness, "clips")["livedub_video_path"] == translated


def test_missing_livedub_file_is_not_passed_and_does_not_force_send(tmp_path: Path) -> None:
    harness = _Harness({"shorts_montage": True})
    missing = tmp_path / "missing.mp4"

    request = _request(
        tmp_path,
        user_mode="eng_fast_qa",
        live_dub_pending=True,
        get_livedub_path=lambda: missing,
    )
    _run(request, harness)

    assert _kwargs(harness, "montage")["livedub_video_path"] is None


def test_rus_mode_does_not_touch_livedub_callbacks(tmp_path: Path) -> None:
    harness = _Harness(ALL_FEATURES)

    def unexpected_get() -> Path | None:
        raise AssertionError("RUS не должен спрашивать LiveDub path")

    _run(_request(tmp_path, get_livedub_path=unexpected_get), harness)

    assert len(harness.calls) == 5


def test_livedub_failure_remains_critical(tmp_path: Path) -> None:
    harness = _Harness(ALL_FEATURES)

    async def failed_send() -> bool:
        raise RuntimeError("livedub failed")

    request = _request(
        tmp_path,
        user_mode="eng",
        live_dub_pending=True,
        send_livedub=failed_send,
    )
    with pytest.raises(RuntimeError, match="livedub failed"):
        _run(request, harness)

    assert harness.calls == []
    assert harness.settings_calls == ["shorts"]


def test_optional_stage_returns_value() -> None:
    async def ok() -> str:
        return "готово"

    assert asyncio.run(run_optional_stage("нужный", ok())) == "готово"


def test_optional_stage_supports_false_and_explicit_none_defaults() -> None:
    async def broken() -> None:
        raise RuntimeError("telegraph лёг")

    assert asyncio.run(run_optional_stage("опциональный", broken())) is False
    assert asyncio.run(run_optional_stage("опциональный", broken(), default=None)) is None


def test_optional_stage_propagates_cancellation() -> None:
    async def cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_optional_stage("стадия", cancelled()))


def test_empty_candidates_do_not_share_mutable_lists() -> None:
    first = empty_candidates()
    second = empty_candidates()

    first["montage_candidates"].append({"id": 1})

    assert second == {"montage_candidates": [], "highlights_candidates": []}


def test_default_dependencies_are_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness({})
    monkeypatch.setattr(extras_stage, "production_deps", harness.deps)

    asyncio.run(run_extras(_request(tmp_path)))

    assert harness.settings_calls == list(ALL_FEATURES)


def test_production_dependencies_keep_runtime_bindings() -> None:
    deps = extras_stage.production_deps()

    assert deps.get_setting.__name__ == "asettings_get"
    assert deps.run_shorts.__name__ == "process_and_send_shorts"
    assert deps.run_clips.__name__ == "process_and_send_clips"
    assert deps.create_candidates.__name__ == "create_extras_candidates"
    assert deps.run_montage.__name__ == "process_and_send_montage"
    assert deps.run_highlights.__name__ == "process_and_send_highlights"


def test_main_pipeline_wires_one_shared_extras_stage() -> None:
    source = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")

    assert source.count("await run_extras(") == 1
    assert "get_livedub_path=lambda: _livedub_video_for_downstream" in source
    assert "send_livedub=_send_livedub_result" in source
    assert "live_dub_pending=bool(live_dub_task and context)" in source
    assert "process_and_send_shorts(" not in source
    assert "process_and_send_clips(" not in source
    assert "create_extras_candidates(" not in source
    assert "process_and_send_montage(" not in source
    assert "process_and_send_highlights(" not in source
