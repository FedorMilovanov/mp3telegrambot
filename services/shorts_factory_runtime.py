#!/usr/bin/env python3
"""Runtime routing and per-task render overrides for Shorts Factory MAX."""
from __future__ import annotations

import copy
import logging
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

DEFAULT_FACTORY_WHISPER_MODEL = "large-v3"

_FACTORY_SHORTS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "factory_shorts_candidates", default=None
)
_FACTORY_LONGS: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "factory_long_candidates", default=None
)
_FACTORY_SETTINGS: ContextVar[dict[str, bool] | None] = ContextVar(
    "factory_render_settings", default=None
)
_FACTORY_SHORT_DELIVERIES: ContextVar[list[int] | None] = ContextVar(
    "factory_short_deliveries", default=None
)
_INSTALLED = False


def factory_subtitle_profile() -> dict[str, Any]:
    """Return the strict subtitle profile used only inside Factory jobs."""
    model_name = (
        os.getenv("SHORTS_FACTORY_WHISPER_MODEL", "").strip()
        or DEFAULT_FACTORY_WHISPER_MODEL
    )
    return {
        "model_name": model_name,
        "karaoke": True,
        "word_timestamps": True,
        "light": False,
        "gemini_hints": True,
    }


def factory_shorts_speed() -> float:
    """Verified Gemini boundaries must not be changed by global speed settings."""
    return 1.0


def factory_short_delivery_count() -> int:
    counter = _FACTORY_SHORT_DELIVERIES.get()
    return int(counter[0]) if counter is not None else 0


def is_subtitled_factory_delivery(video: Any) -> bool:
    """Factory accepts only the final `_sub.mp4` artifact for short delivery."""
    try:
        name = Path(video).name.casefold()
    except (TypeError, ValueError, OSError):
        name = str(getattr(video, "name", "") or "").casefold()
    return name.endswith("_sub.mp4")


class _FactoryMessageProxy:
    """Delegate Telegram calls while rejecting subtitle-less short videos."""

    def __init__(self, message: Any) -> None:
        self._message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._message, name)

    async def reply_video(self, *args, **kwargs):
        video = kwargs.get("video")
        if video is None and args:
            video = args[0]
        if not is_subtitled_factory_delivery(video):
            raise RuntimeError(
                "SHORTS FACTORY rejected subtitle-less delivery artifact; "
                "the candidate was not sent"
            )
        sent = await self._message.reply_video(*args, **kwargs)
        counter = _FACTORY_SHORT_DELIVERIES.get()
        if counter is not None:
            counter[0] += 1
        return sent


class _FactoryUpdateProxy:
    """Expose the original update with a strict message delivery boundary."""

    def __init__(self, update: Any) -> None:
        self._update = update
        self.message = _FactoryMessageProxy(update.message)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._update, name)


@contextmanager
def factory_render_context(
    shorts_candidates: list[dict[str, Any]],
    long_candidates: list[dict[str, Any]],
) -> Iterator[None]:
    """Inject already judged candidates without process-global cross-user state."""
    short_token = _FACTORY_SHORTS.set(copy.deepcopy(shorts_candidates))
    long_token = _FACTORY_LONGS.set(copy.deepcopy(long_candidates))
    delivery_token = _FACTORY_SHORT_DELIVERIES.set([0])
    settings_token = _FACTORY_SETTINGS.set(
        {
            "shorts_subtitles": True,
            "shorts_audio_normalize": True,
            "shorts_snapshot": True,
            "shorts_title_poster": True,
            # Gemini's third pass already verifies exact boundaries. For Yandex
            # the Factory timing policy adds context and the complete live tail.
            "shorts_boundary_padding": False,
            "clips": True,
            # Keep the shared source after Clips so interactive trim buttons can
            # reuse it. The Factory pipeline owns timed cleanup of that cache.
            "shorts_highlights": True,
        }
    )
    try:
        yield
    finally:
        _FACTORY_SETTINGS.reset(settings_token)
        _FACTORY_SHORT_DELIVERIES.reset(delivery_token)
        _FACTORY_LONGS.reset(long_token)
        _FACTORY_SHORTS.reset(short_token)


def install_shorts_factory_mode(_main_module=None) -> bool:
    """Patch both link entry points and reuse the mature render pipelines."""
    global _INSTALLED
    if _INSTALLED:
        return True

    import handlers.commands as commands_module
    import pipelines.clips as clips_module
    import pipelines.playlist as playlist_module
    import pipelines.shorts as shorts_module
    import services.shorts_video_impl as shorts_video_impl_module
    from handlers.mode_command import get_user_mode
    from services.shorts_factory_timing import align_factory_livedub_candidates

    original_process = commands_module.process_single_video
    original_process_shorts = shorts_module.process_and_send_shorts
    original_shorts_candidates = shorts_module.create_shorts_candidates
    original_long_candidates = clips_module.create_clips_candidates
    original_shorts_setting = shorts_module.asettings_get
    original_clips_setting = clips_module.settings_get
    original_shorts_speed = shorts_module.ashorts_speed_get
    original_subtitle_profile = shorts_video_impl_module.get_subtitles_mode_settings

    async def process_link_by_mode(
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
        if mode == "shorts_max":
            if not shorts_video_impl_module.HAS_FASTER_WHISPER:
                message = (
                    "❌ SHORTS FACTORY MAX требует faster-whisper: короткие ролики "
                    "в этом режиме не отправляются без точных вшитых субтитров."
                )
                effective_message = getattr(update, "effective_message", None)
                if effective_message is not None and not silent_errors:
                    try:
                        await effective_message.reply_text(message)
                    except Exception:
                        pass
                logger.error("Shorts Factory rejected: faster-whisper is unavailable")
                return False

            import pipelines.shorts_factory as factory_module

            factory_module._shift_candidates_for_livedub = (
                align_factory_livedub_candidates
            )
            return await factory_module.process_shorts_factory(
                url,
                update,
                status_msg=status_msg,
                progress_prefix=progress_prefix,
                context=context,
                silent_errors=silent_errors,
            )
        return await original_process(
            url,
            update,
            status_msg=status_msg,
            progress_prefix=progress_prefix,
            context=context,
            silent_errors=silent_errors,
        )

    async def factory_process_shorts(*args, **kwargs):
        if _FACTORY_SETTINGS.get() is None:
            return await original_process_shorts(*args, **kwargs)

        before = factory_short_delivery_count()
        if "update" in kwargs:
            call_kwargs = dict(kwargs)
            call_kwargs["update"] = _FactoryUpdateProxy(call_kwargs["update"])
            result = await original_process_shorts(*args, **call_kwargs)
        else:
            call_args = list(args)
            # process_and_send_shorts positional parameter #8 is update.
            if len(call_args) >= 8:
                call_args[7] = _FactoryUpdateProxy(call_args[7])
            result = await original_process_shorts(*call_args, **kwargs)

        if factory_short_delivery_count() <= before:
            raise RuntimeError(
                "SHORTS FACTORY не доставил ни одного Short с вшитыми субтитрами"
            )
        return result

    async def factory_shorts_candidates(*args, **kwargs):
        planned = _FACTORY_SHORTS.get()
        if planned is not None:
            return copy.deepcopy(planned)
        return await original_shorts_candidates(*args, **kwargs)

    async def factory_long_candidates(*args, **kwargs):
        planned = _FACTORY_LONGS.get()
        if planned is not None:
            return copy.deepcopy(planned)
        return await original_long_candidates(*args, **kwargs)

    async def factory_shorts_setting(key: str):
        overrides = _FACTORY_SETTINGS.get()
        if overrides is not None and key in overrides:
            return overrides[key]
        return await original_shorts_setting(key)

    def factory_clips_setting(key: str):
        overrides = _FACTORY_SETTINGS.get()
        if overrides is not None and key in overrides:
            return overrides[key]
        return original_clips_setting(key)

    async def factory_speed_setting():
        if _FACTORY_SETTINGS.get() is not None:
            return factory_shorts_speed()
        return await original_shorts_speed()

    def factory_subtitles_mode_settings():
        if _FACTORY_SETTINGS.get() is not None:
            return factory_subtitle_profile()
        return original_subtitle_profile()

    commands_module.process_single_video = process_link_by_mode
    playlist_module.process_single_video = process_link_by_mode
    shorts_module.process_and_send_shorts = factory_process_shorts
    shorts_module.create_shorts_candidates = factory_shorts_candidates
    clips_module.create_clips_candidates = factory_long_candidates
    shorts_module.asettings_get = factory_shorts_setting
    clips_module.settings_get = factory_clips_setting
    shorts_module.ashorts_speed_get = factory_speed_setting
    shorts_video_impl_module.get_subtitles_mode_settings = factory_subtitles_mode_settings

    # Normally Factory is imported lazily after this installer. Keep strict
    # wrappers and timing correct even under tests or future eager imports.
    eager_factory_module = sys.modules.get("pipelines.shorts_factory")
    if eager_factory_module is not None:
        eager_factory_module.process_and_send_shorts = factory_process_shorts
        eager_factory_module._shift_candidates_for_livedub = (
            align_factory_livedub_candidates
        )

    _INSTALLED = True
    logger.info(
        "Shorts Factory MAX runtime installed: Pro plan, speed=1.0, "
        "Whisper=%s karaoke word-timestamps, subtitle-only delivery, "
        "context-safe Yandex tail",
        factory_subtitle_profile()["model_name"],
    )
    return True


__all__ = [
    "DEFAULT_FACTORY_WHISPER_MODEL",
    "factory_render_context",
    "factory_short_delivery_count",
    "factory_shorts_speed",
    "factory_subtitle_profile",
    "install_shorts_factory_mode",
    "is_subtitled_factory_delivery",
]
