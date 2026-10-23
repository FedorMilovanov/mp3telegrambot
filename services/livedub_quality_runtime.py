#!/usr/bin/env python3
"""Pre-main Gemini/LiveDub policy and network routing.

This module owns configuration only.  It deliberately does not wrap or replace
already-imported service/Telegram functions; LiveDub delivery is explicit in the
pipeline/coordinator and media probing is source-owned by ``livedub_mix``.
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlsplit, urlunsplit

from core.model_catalog import (
    MODEL_PRIMARY as _PRIMARY_MODEL,
    MODEL_UTILITY as _LIGHT_MODEL,
)
from services.env_policy import force, force_all


def configure_gemini_policy() -> str:
    """Pin the semantic/utility split and production LiveDub voice policy."""
    owner = "services.livedub_quality_runtime"
    force_all(
        (
            "GEMINI_MODEL",
            "LIVEDUB_INFO_MODEL",
            "LIVEDUB_QUICK_QA_MODEL",
            "LIVEDUB_LONG_QA_MODEL",
            "LIVEDUB_QA_VERIFY_MODEL",
        ),
        _PRIMARY_MODEL,
        owner=owner,
    )

    force_all(
        (
            "LIVEDUB_QUICK_QA_THINKING",
            "LIVEDUB_LONG_QA_THINKING",
            "LIVEDUB_QA_VERIFY_THINKING",
        ),
        "high",
        owner=owner,
    )
    force("GEMINI_SCHEMA_THINKING", "1", owner=owner)

    force("LIVEDUB_INFO_FALLBACK_MODELS", "", owner=owner)
    force("LIVEDUB_PUBLICATION_FALLBACK_MODELS", "", owner=owner)

    # Production ENG is a quality contract: Yandex Live Voices or a visible
    # failure.  Never let a stale .env/Windows environment value re-enable the
    # legacy live->TTS downgrade path in livedub_mix.
    force("LIVEDUB_TTS_FALLBACK", "0", owner=owner)

    return (
        f"semantic={_PRIMARY_MODEL}/high/no-fallback, "
        f"utility={_LIGHT_MODEL}/minimal/no-fallback, "
        "voice=live-only/no-tts-fallback"
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
    target = _mixed_http_proxy(value).casefold()
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        current = _mixed_http_proxy(os.environ.get(name, "")).casefold()
        if current == target:
            os.environ.pop(name, None)


def configure_gemini_network() -> str:
    """Use an explicit reachable mixed proxy, otherwise rely on system TUN."""
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


def _validate_quality_models() -> None:
    """Prove the native info owner follows the exact semantic route."""
    import services.livedub_info as info

    model = info.get_light_model()
    fallbacks = info.get_light_model_fallbacks()
    if model != _PRIMARY_MODEL or fallbacks:
        raise RuntimeError(
            "LiveDub info owner violated semantic route: "
            f"model={model!r} fallbacks={fallbacks!r}"
        )


__all__ = [
    "configure_gemini_network",
    "configure_gemini_policy",
]
