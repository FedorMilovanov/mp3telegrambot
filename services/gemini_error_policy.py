"""AUDIT R26 — error-aware Gemini retry/fallback policy.

Живой лог (10.07.2026): после КАЖДОГО 429 RESOURCE_EXHAUSTED / 503
UNAVAILABLE / TimeoutError код писал «retry legacy JSON config» и делал
ВТОРОЙ дорогой запрос с другой схемой. Legacy JSON не создаёт новую квоту
и не разгружает сервер — он лишь удваивает нагрузку и быстрее выжигает
free-tier лимит (20 запросов/сутки/модель).

Правило: fallback на legacy JSON оправдан ТОЛЬКО когда отклонён сам
structured output (проблема схемы). При quota/overload/timeout/auth
второй запрос делать нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class GeminiFailure(str, Enum):
    QUOTA = "quota"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    SCHEMA = "schema"
    AUTH = "auth"
    PERMANENT = "permanent"


@dataclass(frozen=True)
class GeminiDecision:
    kind: GeminiFailure
    retry: bool
    use_legacy_json_fallback: bool
    defer_stage: bool
    retry_after_seconds: int | None = None


_RETRY_RE = re.compile(
    r"(?:retry(?:delay)?['\":=\s]+)(?P<seconds>\d+)",
    re.IGNORECASE,
)


def _retry_after(message: str) -> int | None:
    match = _RETRY_RE.search(message)
    return int(match.group("seconds")) if match else None


def classify_gemini_error(exc: BaseException) -> GeminiDecision:
    name = exc.__class__.__name__.casefold()
    message = str(exc)
    lowered = message.casefold()
    retry_after = _retry_after(message)

    if "429" in lowered or "resource_exhausted" in lowered or "quota" in lowered:
        return GeminiDecision(
            kind=GeminiFailure.QUOTA,
            retry=False,
            use_legacy_json_fallback=False,
            defer_stage=True,
            retry_after_seconds=retry_after,
        )

    # AUDIT R26b: не ловим «500» как голое число (напр. «exceeds 500 characters»
    # в 400-ошибке ложно уводило в OVERLOADED). Матчим HTTP-контекст: 503 (одно-
    # значно), «500 internal», структурное 'code': 500/503 — или фразы перегрузки.
    if (re.search(r"\b503\b|\b500\s+internal\b|['\"]?code['\"]?\s*[:=]\s*50[03]\b", lowered)
            or any(t in lowered for t in ("unavailable", "high demand", "overloaded", "internal error"))):
        return GeminiDecision(
            kind=GeminiFailure.OVERLOADED,
            retry=True,
            use_legacy_json_fallback=False,
            defer_stage=False,
            retry_after_seconds=retry_after or 15,
        )

    if "timeout" in name or "timeout" in lowered or "timed out" in lowered:
        return GeminiDecision(
            kind=GeminiFailure.TIMEOUT,
            retry=True,
            use_legacy_json_fallback=False,
            defer_stage=False,
            retry_after_seconds=None,
        )

    if any(t in lowered for t in (
        "response_schema", "structured output", "invalid json schema",
        "schema validation", "json parse", "invalid_argument",
    )):
        return GeminiDecision(
            kind=GeminiFailure.SCHEMA,
            retry=False,
            use_legacy_json_fallback=True,
            defer_stage=False,
            retry_after_seconds=None,
        )

    if any(t in lowered for t in ("401", "403", "permission_denied", "api key not valid")):
        return GeminiDecision(
            kind=GeminiFailure.AUTH,
            retry=False,
            use_legacy_json_fallback=False,
            defer_stage=False,
            retry_after_seconds=None,
        )

    return GeminiDecision(
        kind=GeminiFailure.PERMANENT,
        retry=False,
        use_legacy_json_fallback=False,
        defer_stage=False,
        retry_after_seconds=None,
    )
