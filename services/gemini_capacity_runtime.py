#!/usr/bin/env python3
"""Process-wide Gemini 503 capacity control.

The Google SDK already retries some transport/server failures. Historically each
MP3Bot feature added another retry/key-rotation loop on top, so one backend 503
could fan out into dozens of expensive requests and repeated 50 MB uploads.

This wrapper keeps the configured quality model unchanged while enforcing one
bounded retry policy at the client boundary. Quota (429), auth and ordinary
network errors are deliberately left to the existing callers because they have
different semantics. Repeated genuine 503/high-demand responses open a short
per-model circuit so outer legacy loops fail fast instead of multiplying work.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class GeminiCapacityTerminalError(RuntimeError):
    """Base class for capacity decisions that outer retry loops must not retry."""

    gemini_capacity_terminal = True


class GeminiCapacityCircuitOpen(GeminiCapacityTerminalError):
    """The model backend repeatedly rejected requests and is cooling down."""


class GeminiRequestCapacityRejected(GeminiCapacityTerminalError):
    """The production request was rejected although a tiny probe still worked."""


@dataclass
class _CapacityState:
    open_until: float = 0.0
    reason: str = ""


_STATE: dict[str, _CapacityState] = {}
_STATE_LOCK = threading.Lock()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int((os.getenv(name, str(default)) or str(default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def capacity_max_attempts() -> int:
    return _env_int("GEMINI_CAPACITY_MAX_ATTEMPTS", 3, 1, 4)


def capacity_cooldown_seconds() -> int:
    return _env_int("GEMINI_CAPACITY_COOLDOWN_SECONDS", 120, 15, 900)


def capacity_probe_timeout_seconds() -> int:
    return _env_int("GEMINI_CAPACITY_PROBE_TIMEOUT_SECONDS", 20, 5, 60)


def _capacity_retry_delay(attempt: int) -> float:
    # attempt is the just-failed 1-based attempt. Google recommends exponential
    # backoff for 503. 15s/30s preserved the successful live RUS recovery while
    # avoiding the former key x retry x second-circle request explosion.
    return float(min(60, 15 * (2 ** max(0, attempt - 1))))


def is_backend_capacity_error(exc: BaseException) -> bool:
    """Match genuine Gemini service-capacity responses, not quota/auth errors."""
    text = str(exc).casefold()
    if "429" in text or "resource_exhausted" in text or "quota" in text:
        return False
    return (
        "503" in text
        or "service_unavailable" in text
        or "high demand" in text
        or "temporarily overloaded" in text
    )


def is_capacity_terminal_error(exc: BaseException) -> bool:
    return bool(getattr(exc, "gemini_capacity_terminal", False))


def _model_key(model: Any) -> str:
    return str(model or "unknown").strip() or "unknown"


def _circuit_remaining(model: str) -> tuple[float, str]:
    now = time.monotonic()
    with _STATE_LOCK:
        state = _STATE.get(model)
        if state is None:
            return 0.0, ""
        if state.open_until <= now:
            _STATE.pop(model, None)
            return 0.0, ""
        return state.open_until - now, state.reason


def _open_circuit(model: str, reason: str) -> None:
    with _STATE_LOCK:
        _STATE[model] = _CapacityState(
            open_until=time.monotonic() + capacity_cooldown_seconds(),
            reason=reason,
        )


def _close_circuit(model: str) -> None:
    with _STATE_LOCK:
        _STATE.pop(model, None)


def reset_capacity_state() -> None:
    """Test/diagnostic helper; normal runtime recovers automatically by TTL."""
    with _STATE_LOCK:
        _STATE.clear()


async def _tiny_probe(raw_models: Any, model: str) -> str:
    """Distinguish broad backend saturation from request-class admission failure."""
    try:
        response = await asyncio.wait_for(
            raw_models.generate_content(
                model=model,
                contents="Reply with OK only.",
            ),
            timeout=float(capacity_probe_timeout_seconds()),
        )
        text = str(getattr(response, "text", "") or "").strip()
        return "ok" if text or response is not None else "empty"
    except Exception as exc:
        if is_backend_capacity_error(exc):
            return "capacity"
        logger.warning(
            "Gemini capacity probe failed with non-capacity error: %s: %s",
            type(exc).__name__,
            str(exc)[:220],
        )
        return "other"


async def _generate_with_capacity_control(raw_models: Any, *args: Any, **kwargs: Any) -> Any:
    model = _model_key(kwargs.get("model") or (args[0] if args else ""))
    remaining, reason = _circuit_remaining(model)
    if remaining > 0:
        raise GeminiCapacityCircuitOpen(
            f"Gemini capacity circuit active for {model} ({reason}); "
            f"retry window opens in about {remaining:.0f}s"
        )

    attempts = capacity_max_attempts()
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await raw_models.generate_content(*args, **kwargs)
            _close_circuit(model)
            return result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not is_backend_capacity_error(exc):
                raise
            last_error = exc
            if attempt >= attempts:
                break
            delay = _capacity_retry_delay(attempt)
            logger.warning(
                "Gemini %s capacity 503: bounded retry %d/%d in %.0fs",
                model,
                attempt + 1,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)

    probe = await _tiny_probe(raw_models, model)
    if probe == "ok":
        reason = "production-request admission"
        _open_circuit(model, reason)
        logger.warning(
            "Gemini %s: production request repeatedly rejected but tiny probe passed; "
            "opening %ss anti-stampede circuit",
            model,
            capacity_cooldown_seconds(),
        )
        raise GeminiRequestCapacityRejected(
            f"Gemini capacity gate rejected the production request for {model} "
            "after bounded retries; the tiny diagnostic probe succeeded"
        ) from last_error

    reason = "backend saturation" if probe == "capacity" else "capacity probe inconclusive"
    _open_circuit(model, reason)
    logger.warning(
        "Gemini %s: %s; opening %ss anti-stampede circuit",
        model,
        reason,
        capacity_cooldown_seconds(),
    )
    raise GeminiCapacityCircuitOpen(
        f"Gemini capacity circuit opened for {model} after repeated service saturation"
    ) from last_error


class _AsyncModelsProxy:
    def __init__(self, raw_models: Any):
        self._raw_models = raw_models

    async def generate_content(self, *args: Any, **kwargs: Any) -> Any:
        return await _generate_with_capacity_control(self._raw_models, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_models, name)


class _AsyncClientProxy:
    def __init__(self, raw_aio: Any):
        self._raw_aio = raw_aio
        self._models = _AsyncModelsProxy(raw_aio.models)

    @property
    def models(self) -> _AsyncModelsProxy:
        return self._models

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_aio, name)


class CapacityAwareGeminiClient:
    """Transparent proxy for google.genai.Client async surfaces."""

    def __init__(self, raw_client: Any):
        self._raw_client = raw_client
        self._aio_proxy = _AsyncClientProxy(raw_client.aio)

    @property
    def aio(self) -> _AsyncClientProxy:
        return self._aio_proxy

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_client, name)


def wrap_gemini_client(client: Any) -> Any:
    if client is None or isinstance(client, CapacityAwareGeminiClient):
        return client
    return CapacityAwareGeminiClient(client)


__all__ = [
    "CapacityAwareGeminiClient",
    "GeminiCapacityCircuitOpen",
    "GeminiCapacityTerminalError",
    "GeminiRequestCapacityRejected",
    "capacity_cooldown_seconds",
    "capacity_max_attempts",
    "is_backend_capacity_error",
    "is_capacity_terminal_error",
    "reset_capacity_state",
    "wrap_gemini_client",
]
