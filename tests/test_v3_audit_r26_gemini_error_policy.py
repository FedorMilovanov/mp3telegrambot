#!/usr/bin/env python3
"""AUDIT R26: после 429/503/timeout код генераторов кандидатов (Shorts,
Clips, Extras) делал бессмысленный «retry legacy JSON config» — второй
дорогой запрос, который тоже падал на квоте/перегрузке и лишь быстрее
выжигал free-tier лимит (20/сутки/модель). Fallback на legacy JSON
оправдан ТОЛЬКО при ошибке самой схемы.
"""
from pathlib import Path

from services.gemini_error_policy import (
    GeminiFailure,
    classify_gemini_error,
)


def _err(msg):
    return Exception(msg)


def test_quota_does_not_trigger_legacy_fallback():
    d = classify_gemini_error(_err("429 RESOURCE_EXHAUSTED: quota exceeded"))
    assert d.kind == GeminiFailure.QUOTA
    assert d.use_legacy_json_fallback is False
    assert d.defer_stage is True


def test_overload_does_not_trigger_legacy_fallback():
    d = classify_gemini_error(_err("503 UNAVAILABLE: model is overloaded, high demand"))
    assert d.kind == GeminiFailure.OVERLOADED
    assert d.use_legacy_json_fallback is False


def test_timeout_does_not_trigger_legacy_fallback():
    d = classify_gemini_error(_err("Request timed out"))
    assert d.kind == GeminiFailure.TIMEOUT
    assert d.use_legacy_json_fallback is False


def test_schema_error_DOES_trigger_legacy_fallback():
    d = classify_gemini_error(_err("Invalid JSON schema: response_schema not supported"))
    assert d.kind == GeminiFailure.SCHEMA
    assert d.use_legacy_json_fallback is True


def test_auth_and_permanent_do_not_retry():
    assert classify_gemini_error(_err("403 PERMISSION_DENIED")).kind == GeminiFailure.AUTH
    assert classify_gemini_error(_err("weird unknown error")).kind == GeminiFailure.PERMANENT


def test_retry_after_seconds_parsed():
    d = classify_gemini_error(_err("429 quota, retryDelay: 41s please retry in 41s"))
    assert d.retry_after_seconds == 41


def test_candidate_generators_gate_legacy_retry_on_classifier():
    """Оба генератора кандидатов обязаны спрашивать classify_gemini_error
    перед legacy-JSON retry (а не слепо `if not schema: raise`)."""
    for path in ("services/shorts_candidates.py", "services/render_clips_montage.py"):
        src = Path(path).read_text(encoding="utf-8")
        assert "classify_gemini_error" in src, f"{path} не использует classifier"
        assert "use_legacy_json_fallback" in src, f"{path} не проверяет use_legacy_json_fallback"
