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

_PRIMARY_MODEL = "gemini-3.6-flash"
_UTILITY_FALLBACK_MODEL = "gemini-3.5-flash"
_LIGHT_MODEL = "gemini-3.5-flash-lite"


def configure_gemini_policy() -> str:
    """Pin the semantic/utility split before any AI client/config imports."""
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
