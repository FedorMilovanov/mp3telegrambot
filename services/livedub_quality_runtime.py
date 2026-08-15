#!/usr/bin/env python3
"""Quality-first Gemini and LiveDub delivery runtime used by bot_new.py."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)
_INSTALL_LOCK = threading.Lock()
_AUDIO_LOCK = threading.Lock()
_AUDIO_SENT: dict[tuple[str, ...], float] = {}
_AUDIO_INFLIGHT: dict[tuple[str, ...], Future[bool]] = {}
_RETIRED_MODELS = {
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
}

_PRIMARY_MODEL = "gemini-3.6-flash"
_UTILITY_FALLBACK_MODEL = "gemini-3.5-flash"
_LIGHT_MODEL = "gemini-3.5-flash-lite"


def configure_gemini_policy() -> str:
    """Apply one exact source-owned semantic/utility split before AI imports.

    User-visible LiveDub info, QA and publication copy are exact Gemini 3.6/HIGH.
    Gemini 3.5/Lite is reserved for explicitly mechanical utility work and may
    never re-enter the semantic route through an environment override or fallback.
    """
    for name in (
        "GEMINI_MODEL",
        "GEMINI_MAX_MODEL",
        "LIVEDUB_INFO_MODEL",
        "LIVEDUB_QUICK_QA_MODEL",
        "LIVEDUB_LONG_QA_MODEL",
        "LIVEDUB_QA_VERIFY_MODEL",
    ):
        os.environ[name] = _PRIMARY_MODEL

    for name in (
        "GEMINI_FORCE_THINKING_LEVEL",
        "LIVEDUB_INFO_THINKING",
        "LIVEDUB_QUICK_QA_THINKING",
        "LIVEDUB_LONG_QA_THINKING",
        "LIVEDUB_QA_VERIFY_THINKING",
    ):
        os.environ[name] = "high"
    os.environ["GEMINI_SCHEMA_THINKING"] = "1"

    os.environ["LIVEDUB_INFO_FALLBACK_MODELS"] = ""
    os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""
    os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"

    # Utility lane is also pinned so stale/experimental model IDs cannot turn a
    # mechanical call into an accidental semantic route or surprise quota owner.
    os.environ["GEMINI_LIGHT_MODEL"] = _LIGHT_MODEL
    os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _UTILITY_FALLBACK_MODEL
    os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"

    return (
        f"semantic={_PRIMARY_MODEL}/high/no-fallback, "
        f"utility={_LIGHT_MODEL}->{_UTILITY_FALLBACK_MODEL}/no-main-fallback"
    )


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


def _proxy_reachable(value: str, timeout: float = 0.8) -> bool:
    """Fast TCP check for local mixed ports; remote proxies are trusted."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return True
        if not port:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _clear_dead_proxy(value: str) -> None:
    """Remove only proxy variables pointing at the same dead local endpoint."""
    target = _mixed_http_proxy(value).casefold()
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        current = _mixed_http_proxy(os.environ.get(name, "")).casefold()
        if current == target:
            os.environ.pop(name, None)


def configure_gemini_network() -> str:
    """Use the explicit v2rayN route when alive, otherwise rely on system TUN."""
    explicit = (
        os.getenv("GEMINI_PROXY_URL", "").strip()
        or os.getenv("TELEGRAM_PROXY_URL", "").strip()
    )
    if not explicit:
        return "system route/TUN"

    proxy = _mixed_http_proxy(explicit)
    if not _proxy_reachable(proxy):
        _clear_dead_proxy(proxy)
        return f"system TUN (local proxy {_safe_proxy_label(proxy)} unavailable)"

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


def _install_quality_models() -> None:
    """Verify native LiveDub info owns the semantic route; do not monkey-patch it."""
    import services.livedub_info as info

    model = info.get_light_model()
    fallbacks = info.get_light_model_fallbacks()
    if model != _PRIMARY_MODEL or fallbacks:
        raise RuntimeError(
            "LiveDub info owner violated semantic route: "
            f"model={model!r} fallbacks={fallbacks!r}"
        )
    if not getattr(info.build_livedub_info_card, "_mp3bot_all_clients", False):
        logger.warning(
            "[LiveDubInfo] native request-local multi-client support is missing; "
            "global client rotation remains disabled for concurrency safety"
        )


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


def _prune_audio_sent(now: float, ttl: int) -> None:
    for old_key, saved in list(_AUDIO_SENT.items()):
        if now - saved > ttl:
            _AUDIO_SENT.pop(old_key, None)


def _reserve_audio(
    key: tuple[str, ...], ttl: int = 900
) -> tuple[str, Future[bool] | None]:
    """Return ``sent``, ``wait`` or ``leader`` for one delivery key.

    Only completed successes enter ``_AUDIO_SENT``. A pending Future lets
    concurrent calls share the real Telegram result, while exceptions,
    cancellation and false returns release the key for a genuine retry.
    """
    now = time.monotonic()
    with _AUDIO_LOCK:
        _prune_audio_sent(now, ttl)
        if key in _AUDIO_SENT:
            return "sent", None
        pending = _AUDIO_INFLIGHT.get(key)
        if pending is not None:
            return "wait", pending
        pending = Future()
        _AUDIO_INFLIGHT[key] = pending
        return "leader", pending


def _complete_audio(
    key: tuple[str, ...], pending: Future[bool], success: bool
) -> None:
    with _AUDIO_LOCK:
        if _AUDIO_INFLIGHT.get(key) is pending:
            _AUDIO_INFLIGHT.pop(key, None)
        if success:
            _AUDIO_SENT[key] = time.monotonic()
    if not pending.done():
        pending.set_result(success)


async def _run_audio_once(
    key: tuple[str, ...],
    label: str,
    sender: Callable[[], Awaitable[Any]],
) -> Any:
    state, pending = _reserve_audio(key)
    if state == "sent":
        logger.info(
            "[LiveDubAudio] duplicate %s suppressed after confirmed success", label
        )
        return True
    if state == "wait":
        assert pending is not None
        success = bool(await asyncio.wrap_future(pending))
        logger.info(
            "[LiveDubAudio] concurrent %s joined existing delivery: %s",
            label,
            "success" if success else "failed",
        )
        return success

    assert pending is not None
    try:
        result = await sender()
    except BaseException:
        _complete_audio(key, pending, False)
        raise
    success = bool(result)
    _complete_audio(key, pending, success)
    if not success:
        logger.warning("[LiveDubAudio] %s returned false; retry key released", label)
    return result


def _claim_audio(key: tuple[str, ...], ttl: int = 900) -> bool:
    """Side-effect-free compatibility probe for older diagnostics."""
    now = time.monotonic()
    with _AUDIO_LOCK:
        _prune_audio_sent(now, ttl)
        return key not in _AUDIO_SENT and key not in _AUDIO_INFLIGHT


def _install_audio_once() -> None:
    import services.livedub_audio_companion as companion

    current_new = companion._send_new_audio
    if not getattr(current_new, "_mp3bot_once", False):

        async def send_new_once(*args, **kwargs):
            key = _audio_key("new", kwargs)
            return await _run_audio_once(
                key,
                "new dual-MP3 set",
                lambda: current_new(*args, **kwargs),
            )

        send_new_once._mp3bot_once = True  # type: ignore[attr-defined]
        companion._send_new_audio = send_new_once

    current_cached = companion._send_cached_audio
    if not getattr(current_cached, "_mp3bot_once", False):

        async def send_cached_once(*args, **kwargs):
            key = _audio_key("cached", kwargs)
            return await _run_audio_once(
                key,
                "cached dual-MP3 set",
                lambda: current_cached(*args, **kwargs),
            )

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
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height:format=duration",
                    "-of",
                    "json",
                    str(path),
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
            logger.warning(
                "[LiveDubMix] UTF-8 ffprobe fallback: %s", str(exc)[:160]
            )
            return current(path)

    utf8_probe._mp3bot_utf8 = True  # type: ignore[attr-defined]
    mix.probe_video_meta = utf8_probe


def install_livedub_quality_runtime() -> str:
    """Compatibility validator; delivery/probe ownership is now explicit."""
    _install_quality_models()
    return (
        "semantic=Gemini 3.6/HIGH/no-fallback; explicit LiveDub coordinator; "
        "source-owned UTF-8 probes"
    )
