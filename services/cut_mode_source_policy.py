#!/usr/bin/env python3
"""Fail-closed translated-source and cache contract for legacy cut modes."""
from __future__ import annotations

import copy
import functools
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from core.database import asettings_get_all
from services.media_delivery_probe import (
    media_probe_is_deliverable,
    probe_media_async,
)

logger = logging.getLogger(__name__)

_ENG_CUT_MODES = frozenset({"eng", "eng_fast", "eng_fast_qa"})
_CUT_SETTING_KEYS = (
    "shorts",
    "clips",
    "shorts_montage",
    "shorts_highlights",
)
_REPLAY_DISABLED_SETTINGS = frozenset(
    {
        "synopsis",
        "analytics",
        "questions",
        "terms",
        "study_analysis",
        "reflection_application",
        "caption_full_text",
        "generate_pdf",
        "generate_quiz",
    }
)
_CUT_SOURCE_MODE: ContextVar[str] = ContextVar(
    "legacy_cut_source_mode",
    default="rus",
)
_CUT_PIPELINE_REQUESTED: ContextVar[bool] = ContextVar(
    "legacy_cut_pipeline_requested",
    default=False,
)
_CUT_CACHE_REPLAY: ContextVar[bool] = ContextVar(
    "legacy_cut_cache_replay",
    default=False,
)
_CUT_CACHED_RECORD: ContextVar[dict[str, Any] | None] = ContextVar(
    "legacy_cut_cached_record",
    default=None,
)
_INSTALLED = False


def normalized_cut_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode else "rus"


def translated_source_required(mode: str | None = None) -> bool:
    current = normalized_cut_mode(
        _CUT_SOURCE_MODE.get() if mode is None else mode
    )
    return current in _ENG_CUT_MODES


def cut_pipeline_requested() -> bool:
    return bool(_CUT_PIPELINE_REQUESTED.get())


def cut_cache_replay_active() -> bool:
    return bool(_CUT_CACHE_REPLAY.get())


def cut_cache_validity(
    valid: bool,
    reason: str,
    *,
    pipeline_requested: bool,
) -> tuple[bool, str]:
    """Enabled render stages must not disappear behind an early cache return."""
    if valid and pipeline_requested:
        return False, "cut_cache_replay"
    return bool(valid), str(reason or "")


def cached_record_for_cut_source(
    cached: dict[str, Any] | None,
    *,
    pipeline_requested: bool,
    translated_required: bool,
) -> dict[str, Any] | None:
    """A Telegram file_id cannot be cut; force a local LiveDub rebuild."""
    if not cached:
        return cached
    if (
        pipeline_requested
        and translated_required
        and cached.get("livedub_file_id")
    ):
        return {
            **cached,
            "livedub_file_id": "",
            "livedub_file_id_version": "",
        }
    return cached


def cut_replay_setting_value(key: str, value: bool) -> bool:
    if cut_cache_replay_active() and key in _REPLAY_DISABLED_SETTINGS:
        return False
    return bool(value)


def cut_replay_settings(settings: dict[str, bool]) -> dict[str, bool]:
    result = dict(settings or {})
    if cut_cache_replay_active():
        for key in _REPLAY_DISABLED_SETTINGS:
            result[key] = False
    return result


def cut_source_is_usable(
    livedub_video_path: Any,
    *,
    mode: str | None = None,
) -> bool:
    """Return whether a cut may run under the current persisted link mode."""
    if not translated_source_required(mode):
        return True
    if not livedub_video_path:
        return False
    try:
        return Path(livedub_video_path).is_file()
    except (OSError, TypeError, ValueError):
        return False


@contextmanager
def cut_source_mode_context(
    mode: str,
    *,
    pipeline_requested: bool = False,
) -> Iterator[None]:
    mode_token = _CUT_SOURCE_MODE.set(normalized_cut_mode(mode))
    requested_token = _CUT_PIPELINE_REQUESTED.set(bool(pipeline_requested))
    replay_token = _CUT_CACHE_REPLAY.set(False)
    cached_token = _CUT_CACHED_RECORD.set(None)
    try:
        yield
    finally:
        _CUT_CACHED_RECORD.reset(cached_token)
        _CUT_CACHE_REPLAY.reset(replay_token)
        _CUT_PIPELINE_REQUESTED.reset(requested_token)
        _CUT_SOURCE_MODE.reset(mode_token)


class _CutReplayMessageProxy:
    """Suppress duplicate MP3 delivery while delegating status and videos."""

    def __init__(self, message: Any) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply_audio(self, *args, **kwargs):
        if cut_cache_replay_active():
            logger.info(
                "Cached cut replay: suppressing duplicate MP3 delivery"
            )
            return SimpleNamespace(audio=None)
        return await self._message.reply_audio(*args, **kwargs)


class _CutReplayUpdateProxy:
    def __init__(self, update: Any) -> None:
        self._update = update
        self.message = _CutReplayMessageProxy(update.message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._update, name)


class _ClipMessageProxy:
    """Bind Telegram metadata to the proved rendered Clip, not the AI plan."""

    def __init__(self, message: Any) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply_video(self, *args, **kwargs):
        video = kwargs.get("video")
        if video is None and args:
            video = args[0]
        try:
            path = Path(video)
        except (TypeError, ValueError, OSError) as exc:
            raise RuntimeError("Clip delivery path is invalid") from exc

        probe = await probe_media_async(path)
        if not media_probe_is_deliverable(probe):
            raise RuntimeError(
                "Clip delivery rejected: final file lacks proved video+audio"
            )
        assert probe is not None
        kwargs["duration"] = max(1, int(round(probe.duration)))
        logger.info(
            "Clip delivery metadata proved: file=%s duration=%.3fs",
            path.name,
            probe.duration,
        )
        return await self._message.reply_video(*args, **kwargs)


class _ClipUpdateProxy:
    def __init__(self, update: Any) -> None:
        self._update = update
        self.message = _ClipMessageProxy(update.message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._update, name)


def install_cut_mode_source_policy() -> bool:
    """Bind task-local cut intent and reject misleading cache/source fallbacks."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import pipelines.clips as clips_module
    import pipelines.main_pipeline as main_pipeline_module
    import pipelines.montage as montage_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts as shorts_module
    from handlers.mode_command import get_user_mode

    original_main_process = main_pipeline_module.process_single_video
    original_commands_process = commands_module.process_single_video
    original_playlist_process = playlist_module.process_single_video
    original_adb_get = main_pipeline_module.adb_get
    original_adb_save = main_pipeline_module.adb_save
    original_is_cache_valid = main_pipeline_module.is_cache_valid
    original_asettings_get = main_pipeline_module.asettings_get
    original_asettings_get_all = main_pipeline_module.asettings_get_all
    original_gemini_analyze_audio = main_pipeline_module.gemini_analyze_audio
    original_save_archive = main_pipeline_module.asave_generated_page_record
    original_save_segments = main_pipeline_module.asave_segment_plan_export
    original_update_repair = main_pipeline_module.aupdate_generated_page_repair_status
    original_shorts = shorts_module.process_and_send_shorts
    original_clips = clips_module.process_and_send_clips
    original_montage = montage_module.process_and_send_montage
    original_highlights = montage_module.process_and_send_highlights

    async def _cut_features_requested() -> bool:
        try:
            settings = await asettings_get_all()
        except Exception as exc:
            logger.warning(
                "Cut settings read failed; preserving ordinary cache behavior: %s",
                str(exc)[:160],
            )
            return False
        return any(bool(settings.get(key, False)) for key in _CUT_SETTING_KEYS)

    def _wrap_process_entry(original_process):
        @functools.wraps(original_process)
        async def process_single_video_with_mode(
            url,
            update,
            status_msg=None,
            progress_prefix="",
            context=None,
            silent_errors: bool = False,
        ):
            user = getattr(update, "effective_user", None)
            user_id = int(getattr(user, "id", 0) or 0)
            mode = await get_user_mode(user_id) if user_id else "rus"
            requested = await _cut_features_requested()
            with cut_source_mode_context(
                mode,
                pipeline_requested=requested,
            ):
                wrapped_update = _CutReplayUpdateProxy(update)
                return await original_process(
                    url,
                    wrapped_update,
                    status_msg=status_msg,
                    progress_prefix=progress_prefix,
                    context=context,
                    silent_errors=silent_errors,
                )

        return process_single_video_with_mode

    main_process_with_mode = _wrap_process_entry(original_main_process)
    commands_process_with_mode = _wrap_process_entry(original_commands_process)
    playlist_process_with_mode = _wrap_process_entry(original_playlist_process)

    async def cut_aware_adb_get(video_id):
        cached = await original_adb_get(video_id)
        if cached:
            _CUT_CACHED_RECORD.set(copy.deepcopy(cached))
        adjusted = cached_record_for_cut_source(
            cached,
            pipeline_requested=cut_pipeline_requested(),
            translated_required=translated_source_required(),
        )
        if adjusted is not cached:
            logger.info(
                "Ignoring cached LiveDub file_id for %s because enabled cut "
                "modes require a local translated video source",
                video_id,
            )
        return adjusted

    def cut_aware_is_cache_valid(cached):
        valid, reason = original_is_cache_valid(cached)
        adjusted = cut_cache_validity(
            valid,
            reason,
            pipeline_requested=cut_pipeline_requested(),
        )
        if valid and not adjusted[0]:
            _CUT_CACHE_REPLAY.set(True)
            logger.info(
                "Valid analysis cache converted to cut replay: reusing ai_data "
                "without duplicate pages or MP3 delivery"
            )
        return adjusted

    async def cut_aware_settings_get(key):
        value = await original_asettings_get(key)
        return cut_replay_setting_value(key, value)

    async def cut_aware_settings_get_all():
        settings = await original_asettings_get_all()
        return cut_replay_settings(settings)

    async def cut_aware_gemini_analyze(*args, **kwargs):
        if cut_cache_replay_active():
            cached = _CUT_CACHED_RECORD.get() or {}
            ai_data = cached.get("ai_data")
            if isinstance(ai_data, dict) and ai_data:
                logger.info(
                    "Cached cut replay: reusing ai_data without Gemini upload/call"
                )
                return copy.deepcopy(ai_data), None, None
            raise RuntimeError(
                "Cut cache replay was selected without reusable ai_data"
            )
        return await original_gemini_analyze_audio(*args, **kwargs)

    async def cut_aware_adb_save(*args, **kwargs):
        if cut_cache_replay_active():
            logger.info(
                "Cached cut replay: preserving existing video_cache record"
            )
            return None
        return await original_adb_save(*args, **kwargs)

    async def cut_aware_archive_save(*args, **kwargs):
        if cut_cache_replay_active():
            return None
        return await original_save_archive(*args, **kwargs)

    async def cut_aware_segment_export(*args, **kwargs):
        if cut_cache_replay_active():
            return {"count": "0", "replay": "1"}
        return await original_save_segments(*args, **kwargs)

    async def cut_aware_repair_update(*args, **kwargs):
        if cut_cache_replay_active():
            return None
        return await original_update_repair(*args, **kwargs)

    def _skip_reason(kind: str) -> None:
        logger.warning(
            "%s skipped fail-closed: mode=%s requires a verified translated "
            "LiveDub source; original-language fallback is forbidden",
            kind,
            _CUT_SOURCE_MODE.get(),
        )

    @functools.wraps(original_shorts)
    async def guarded_shorts(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 14:
            path = args[13]
        if not cut_source_is_usable(path):
            _skip_reason("Shorts")
            return None
        return await original_shorts(*args, **kwargs)

    @functools.wraps(original_clips)
    async def guarded_clips(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 13:
            path = args[12]
        if not cut_source_is_usable(path):
            _skip_reason("Clips")
            return None

        if "update" in kwargs:
            call_kwargs = dict(kwargs)
            call_kwargs["update"] = _ClipUpdateProxy(call_kwargs["update"])
            return await original_clips(*args, **call_kwargs)

        call_args = list(args)
        if len(call_args) >= 8:
            call_args[7] = _ClipUpdateProxy(call_args[7])
        return await original_clips(*call_args, **kwargs)

    @functools.wraps(original_montage)
    async def guarded_montage(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 14:
            path = args[13]
        if not cut_source_is_usable(path):
            _skip_reason("Montage")
            return None
        return await original_montage(*args, **kwargs)

    @functools.wraps(original_highlights)
    async def guarded_highlights(*args, **kwargs):
        path = kwargs.get("livedub_video_path")
        if path is None and len(args) >= 14:
            path = args[13]
        if not cut_source_is_usable(path):
            _skip_reason("Highlights")
            return None
        return await original_highlights(*args, **kwargs)

    main_pipeline_module.process_single_video = main_process_with_mode
    commands_module.process_single_video = commands_process_with_mode
    playlist_module.process_single_video = playlist_process_with_mode
    main_pipeline_module.adb_get = cut_aware_adb_get
    main_pipeline_module.adb_save = cut_aware_adb_save
    main_pipeline_module.is_cache_valid = cut_aware_is_cache_valid
    main_pipeline_module.asettings_get = cut_aware_settings_get
    main_pipeline_module.asettings_get_all = cut_aware_settings_get_all
    main_pipeline_module.gemini_analyze_audio = cut_aware_gemini_analyze
    main_pipeline_module.asave_generated_page_record = cut_aware_archive_save
    main_pipeline_module.asave_segment_plan_export = cut_aware_segment_export
    main_pipeline_module.aupdate_generated_page_repair_status = cut_aware_repair_update

    shorts_module.process_and_send_shorts = guarded_shorts
    clips_module.process_and_send_clips = guarded_clips
    montage_module.process_and_send_montage = guarded_montage
    montage_module.process_and_send_highlights = guarded_highlights

    main_pipeline_module.process_and_send_shorts = guarded_shorts
    main_pipeline_module.process_and_send_clips = guarded_clips
    main_pipeline_module.process_and_send_montage = guarded_montage
    main_pipeline_module.process_and_send_highlights = guarded_highlights

    _INSTALLED = True
    logger.info(
        "Legacy cut policy installed: valid cache becomes no-publication cut "
        "replay, local LiveDub is required for ENG, and untranslated fallbacks "
        "are forbidden"
    )
    return True


__all__ = [
    "cached_record_for_cut_source",
    "cut_cache_replay_active",
    "cut_cache_validity",
    "cut_pipeline_requested",
    "cut_replay_setting_value",
    "cut_replay_settings",
    "cut_source_is_usable",
    "cut_source_mode_context",
    "install_cut_mode_source_policy",
    "normalized_cut_mode",
    "translated_source_required",
]
