#!/usr/bin/env python3
"""Quality-first LiveDub delivery fixes used by bot_new.py."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)
_INSTALL_LOCK = threading.Lock()
_AUDIO_LOCK = threading.Lock()
_AUDIO_SENT: dict[tuple[str, ...], float] = {}
_RETIRED_MODELS = {
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
}


def _mixed_http_proxy(value: str) -> str:
    value = str(value or "").strip()
    if value.lower().startswith(("socks5h://", "socks5://", "socks4://")):
        return "http://" + value.split("://", 1)[1]
    return value


def _safe_proxy_label(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit((parsed.scheme, f"{parsed.hostname or ''}{port}", "", "", ""))
    except Exception:
        return "configured proxy"


def configure_gemini_network() -> str:
    """Force google-genai through the explicitly configured v2rayN route."""
    explicit = (
        os.getenv("GEMINI_PROXY_URL", "").strip()
        or os.getenv("TELEGRAM_PROXY_URL", "").strip()
    )
    if not explicit:
        return ""
    proxy = _mixed_http_proxy(explicit)
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        os.environ[name] = proxy
    no_proxy = [
        item.strip()
        for item in os.environ.get("NO_PROXY", "").split(",")
        if item.strip()
        and "googleapis.com" not in item.casefold()
        and "generativelanguage" not in item.casefold()
    ]
    for host in ("127.0.0.1", "localhost", "::1"):
        if host not in no_proxy:
            no_proxy.append(host)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = ",".join(no_proxy)
    return _safe_proxy_label(proxy)


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in out and value not in _RETIRED_MODELS:
            out.append(value)
    return out


def _install_quality_models() -> None:
    import services.livedub_info as info
    from core.database import GEMINI_MODEL

    # Full LiveDub descriptions are a publication artifact, so quality wins.
    # The main deep-analysis model remains untouched elsewhere in the project.
    def quality_model() -> str:
        configured = os.getenv("LIVEDUB_INFO_MODEL", "gemini-3.6-flash").strip()
        return configured if configured not in _RETIRED_MODELS else "gemini-3.6-flash"

    def quality_fallbacks() -> list[str]:
        raw = os.getenv(
            "LIVEDUB_INFO_FALLBACK_MODELS",
            "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite",
        )
        return [
            model
            for model in _unique([GEMINI_MODEL, *raw.split(",")])
            if model != quality_model()
        ]

    info.DEFAULT_LIGHT_MODEL = "gemini-3.6-flash"
    info.get_light_model = quality_model
    info.get_light_model_fallbacks = quality_fallbacks

    current = info.build_livedub_info_card
    if getattr(current, "_mp3bot_all_clients", False):
        return

    async def all_clients(title_line, dub_srt_path=None, *, source_url="", force=False):
        from core.globals import GEMINI_CLIENTS

        if len(GEMINI_CLIENTS) <= 1:
            return await current(
                title_line, dub_srt_path, source_url=source_url, force=force
            )
        original_order = list(GEMINI_CLIENTS)
        best = None
        try:
            for client in original_order:
                GEMINI_CLIENTS[:] = [client, *[x for x in original_order if x is not client]]
                card = await current(
                    title_line, dub_srt_path, source_url=source_url, force=force
                )
                best = card or best
                if isinstance(card, dict) and card.get("source") != "metadata_fallback":
                    return card
            return best
        finally:
            GEMINI_CLIENTS[:] = original_order

    all_clients._mp3bot_all_clients = True  # type: ignore[attr-defined]
    info.build_livedub_info_card = all_clients


def _audio_key(kind: str, kwargs: dict[str, Any]) -> tuple[str, ...]:
    source = kwargs.get("video_path")
    try:
        source = str(Path(source).resolve()) if source else ""
    except Exception:
        source = str(source or "")
    return (
        kind,
        str(kwargs.get("chat_id") or ""),
        str(kwargs.get("reply_to") or ""),
        str(kwargs.get("video_file_id") or ""),
        source,
    )


def _claim_audio(key: tuple[str, ...], ttl: int = 900) -> bool:
    now = time.monotonic()
    with _AUDIO_LOCK:
        for old_key, saved in list(_AUDIO_SENT.items()):
            if now - saved > ttl:
                _AUDIO_SENT.pop(old_key, None)
        if key in _AUDIO_SENT:
            return False
        _AUDIO_SENT[key] = now
        return True


def _install_audio_once() -> None:
    import services.livedub_audio_companion as companion

    current_new = companion._send_new_audio
    if not getattr(current_new, "_mp3bot_once", False):
        async def send_new_once(*args, **kwargs):
            key = _audio_key("new", kwargs)
            if not _claim_audio(key):
                logger.info("[LiveDubAudio] duplicate Russian MP3 suppressed")
                return True
            return await current_new(*args, **kwargs)

        send_new_once._mp3bot_once = True  # type: ignore[attr-defined]
        companion._send_new_audio = send_new_once

    current_cached = companion._send_cached_audio
    if not getattr(current_cached, "_mp3bot_once", False):
        async def send_cached_once(*args, **kwargs):
            key = _audio_key("cached", kwargs)
            if not _claim_audio(key):
                logger.info("[LiveDubAudio] duplicate cached Russian MP3 suppressed")
                return True
            return await current_cached(*args, **kwargs)

        send_cached_once._mp3bot_once = True  # type: ignore[attr-defined]
        companion._send_cached_audio = send_cached_once


def _install_utf8_probe() -> None:
    import services.livedub_mix as mix

    current = mix.probe_video_meta
    if getattr(current, "_mp3bot_utf8", False):
        return

    def utf8_probe(path: Path) -> dict:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return current(path)
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 60,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.run(
                [
                    ffprobe, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height:format=duration",
                    "-of", "json", str(path),
                ],
                **kwargs,
            )
            data = json.loads(proc.stdout or "{}")
            streams = data.get("streams") or [{}]
            duration = (data.get("format") or {}).get("duration")
            return {
                "width": streams[0].get("width"),
                "height": streams[0].get("height"),
                "duration": int(float(duration)) if duration else None,
            }
        except Exception as exc:
            logger.warning("[LiveDubMix] UTF-8 ffprobe fallback: %s", str(exc)[:160])
            return current(path)

    utf8_probe._mp3bot_utf8 = True  # type: ignore[attr-defined]
    mix.probe_video_meta = utf8_probe


def install_livedub_quality_runtime() -> None:
    with _INSTALL_LOCK:
        _install_quality_models()
        _install_audio_once()
        _install_utf8_probe()
        logger.info(
            "✨ LiveDub quality runtime: ✅ Gemini route, 3.6→3.5 quality cascade, "
            "one Russian MP3, UTF-8 ffprobe"
        )
