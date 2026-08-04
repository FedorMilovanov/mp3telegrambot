#!/usr/bin/env python3
"""Truthful Telegram delivery evidence for cached legacy cut replay."""
from __future__ import annotations

import functools
import logging
from contextvars import ContextVar
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPLAY_OCCURRED: ContextVar[bool] = ContextVar(
    "cut_replay_delivery_occurred",
    default=False,
)
_REPLAY_DELIVERIES: ContextVar[list[int] | None] = ContextVar(
    "cut_replay_telegram_deliveries",
    default=None,
)
_INSTALLED = False


def cut_replay_delivery_count() -> int:
    counter = _REPLAY_DELIVERIES.get()
    return int(counter[0]) if counter is not None else 0


def same_replay_audio_path(left: Any, right: Path | None) -> bool:
    """Compare Path values and Telegram file objects by their concrete name."""
    if left is None or right is None:
        return False
    candidate = getattr(left, "name", left)
    try:
        return Path(candidate).resolve(strict=False) == right.resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return False


def mark_cut_replay_from_cache_decision(
    valid: bool,
    adjusted: tuple[bool, str],
) -> tuple[bool, str]:
    """Persist replay evidence outside the source-policy context lifetime."""
    if valid and adjusted == (False, "cut_cache_replay"):
        _REPLAY_OCCURRED.set(True)
    return adjusted


class _CutDeliveryMessageProxy:
    """Count only videos emitted from a cut pipeline wrapper."""

    def __init__(self, message: Any) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply_video(self, *args, **kwargs):
        sent = await self._message.reply_video(*args, **kwargs)
        if _REPLAY_OCCURRED.get():
            counter = _REPLAY_DELIVERIES.get()
            if counter is not None:
                counter[0] += 1
        return sent


class _CutDeliveryUpdateProxy:
    def __init__(self, update: Any) -> None:
        self._update = update
        self.message = _CutDeliveryMessageProxy(update.message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._update, name)


def install_cut_replay_delivery_policy() -> bool:
    """Require at least one Telegram-accepted cut during a cached replay."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import pipelines.clips as clips_module
    import pipelines.main_pipeline as main_pipeline_module
    import pipelines.montage as montage_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts as shorts_module
    import services.cut_mode_source_policy as source_policy

    original_cache_validity = source_policy.cut_cache_validity
    original_main_process = main_pipeline_module.process_single_video
    original_commands_process = commands_module.process_single_video
    original_playlist_process = playlist_module.process_single_video
    original_shorts = shorts_module.process_and_send_shorts
    original_clips = clips_module.process_and_send_clips
    original_montage = montage_module.process_and_send_montage
    original_highlights = montage_module.process_and_send_highlights

    @functools.wraps(original_cache_validity)
    def cache_validity_with_replay_evidence(*args, **kwargs):
        valid = bool(args[0] if args else kwargs.get("valid", False))
        adjusted = original_cache_validity(*args, **kwargs)
        return mark_cut_replay_from_cache_decision(valid, adjusted)

    def _wrap_entry(original_process):
        @functools.wraps(original_process)
        async def process_with_delivery_evidence(
            url,
            update,
            status_msg=None,
            progress_prefix="",
            context=None,
            silent_errors: bool = False,
        ):
            replay_token = _REPLAY_OCCURRED.set(False)
            delivery_token = _REPLAY_DELIVERIES.set([0])
            try:
                result = await original_process(
                    url,
                    update,
                    status_msg=status_msg,
                    progress_prefix=progress_prefix,
                    context=context,
                    silent_errors=silent_errors,
                )
                if not _REPLAY_OCCURRED.get():
                    return result

                delivered = cut_replay_delivery_count()
                if delivered <= 0:
                    logger.error(
                        "Cached cut replay completed without a Telegram-accepted "
                        "Shorts/Clips/Montage/Highlights video"
                    )
                    if not silent_errors:
                        try:
                            await update.message.reply_text(
                                "❌ Кэшированный анализ найден, но ни один "
                                "включённый видеофрагмент не был доставлен."
                            )
                        except Exception:
                            pass
                    return False

                logger.info(
                    "Cached cut replay delivered %d cut video(s)",
                    delivered,
                )
                return True
            finally:
                _REPLAY_DELIVERIES.reset(delivery_token)
                _REPLAY_OCCURRED.reset(replay_token)

        return process_with_delivery_evidence

    def _wrap_cut(original_process):
        @functools.wraps(original_process)
        async def process_with_cut_delivery_proxy(*args, **kwargs):
            if "update" in kwargs:
                call_kwargs = dict(kwargs)
                call_kwargs["update"] = _CutDeliveryUpdateProxy(
                    call_kwargs["update"]
                )
                return await original_process(*args, **call_kwargs)

            call_args = list(args)
            if len(call_args) >= 8:
                call_args[7] = _CutDeliveryUpdateProxy(call_args[7])
            return await original_process(*call_args, **kwargs)

        return process_with_cut_delivery_proxy

    main_process = _wrap_entry(original_main_process)
    commands_process = _wrap_entry(original_commands_process)
    playlist_process = _wrap_entry(original_playlist_process)
    shorts_process = _wrap_cut(original_shorts)
    clips_process = _wrap_cut(original_clips)
    montage_process = _wrap_cut(original_montage)
    highlights_process = _wrap_cut(original_highlights)

    source_policy._same_file_path = same_replay_audio_path
    source_policy.cut_cache_validity = cache_validity_with_replay_evidence
    main_pipeline_module.process_single_video = main_process
    commands_module.process_single_video = commands_process
    playlist_module.process_single_video = playlist_process

    shorts_module.process_and_send_shorts = shorts_process
    clips_module.process_and_send_clips = clips_process
    montage_module.process_and_send_montage = montage_process
    montage_module.process_and_send_highlights = highlights_process

    main_pipeline_module.process_and_send_shorts = shorts_process
    main_pipeline_module.process_and_send_clips = clips_process
    main_pipeline_module.process_and_send_montage = montage_process
    main_pipeline_module.process_and_send_highlights = highlights_process

    _INSTALLED = True
    logger.info(
        "Cached cut replay delivery policy installed: only Telegram-accepted "
        "cut videos count as success"
    )
    return True


__all__ = [
    "cut_replay_delivery_count",
    "install_cut_replay_delivery_policy",
    "mark_cut_replay_from_cache_decision",
    "same_replay_audio_path",
]
