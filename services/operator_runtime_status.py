#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Privacy-safe runtime and production-TTS diagnostics for the admin /status.

The runtime manifest retains installer details for local debugging, but those
strings may contain paths or configuration fragments.  This module deliberately
publishes only aggregate states and stable identifiers.  TTS is reported as the
production *default* because each Dub project durably pins its own profile.
"""
from __future__ import annotations

import functools
import html
import threading
from types import ModuleType
from typing import Any, Mapping

OPERATOR_RUNTIME_STATUS_POLICY = "privacy-safe-operator-runtime-status-v1"
_INSTALLED = False
_LOCK = threading.Lock()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} должен быть mapping.")
    return value


def _safe_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160:
        raise RuntimeError(f"{label} отсутствует или слишком длинный.")
    if any(ord(character) < 32 for character in text):
        raise RuntimeError(f"{label} содержит управляющие символы.")
    return text


def _sha_prefix(value: object) -> str:
    text = str(value or "").strip().casefold()
    if not text:
        return "—"
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise RuntimeError("Status provenance SHA должен быть 64-символьным hex.")
    return text[:12]


def operator_runtime_status_payload(
    *,
    runtime_payload: Mapping[str, Any] | None = None,
    profile: Any | None = None,
    backend: Any | None = None,
    source_evidence: Mapping[str, Any] | None = None,
    registered_profiles: tuple[Any, ...] | None = None,
    contract: Any | None = None,
) -> dict[str, Any]:
    """Return one strict allowlist without installer details or model paths."""
    if runtime_payload is None:
        from services.runtime_manifest import runtime_manifest_payload

        runtime_payload = runtime_manifest_payload()
    runtime = _mapping(runtime_payload, "runtime manifest")
    features = _mapping(runtime.get("features"), "runtime manifest features")

    state_counts = {"installed": 0, "pending": 0, "failed": 0, "skipped": 0}
    required_failures: list[str] = []
    optional_degraded: list[str] = []
    for raw_feature_id, raw_result in features.items():
        feature_id = _safe_id(raw_feature_id, "runtime feature_id")
        result = _mapping(raw_result, f"runtime feature {feature_id}")
        state = str(result.get("state") or "").strip().casefold()
        if state not in state_counts:
            raise RuntimeError(f"Runtime feature {feature_id} имеет неизвестный state.")
        state_counts[state] += 1
        required = result.get("required") is True
        if required and state != "installed":
            required_failures.append(feature_id)
        if not required and state in {"failed", "skipped"}:
            optional_degraded.append(feature_id)

    required_ready = runtime.get("required_ready") is True
    if required_ready != (not required_failures):
        raise RuntimeError("Runtime manifest required_ready не совпадает с feature states.")

    if profile is None or backend is None or source_evidence is None or registered_profiles is None:
        from services.speech_backends import (
            default_backend,
            default_model_profile,
            model_profile_source_evidence,
            registered_model_profiles,
        )

        profile = profile or default_model_profile()
        backend = backend or default_backend()
        source_evidence = source_evidence or model_profile_source_evidence(
            profile.profile_id
        )
        registered_profiles = registered_profiles or registered_model_profiles()
    if contract is None:
        from services.speech_backends import get_backend_model_contract

        contract = get_backend_model_contract(profile.backend_id)

    contract.validate_profile(profile)
    if str(getattr(backend, "backend_id", "") or "").strip() != str(
        getattr(profile, "backend_id", "") or ""
    ).strip():
        raise RuntimeError("Default TTS backend не совпадает с default profile.")

    source = _mapping(source_evidence, "TTS profile source evidence")
    profile_id = _safe_id(getattr(profile, "profile_id", ""), "profile_id")
    backend_id = _safe_id(getattr(profile, "backend_id", ""), "backend_id")
    if str(source.get("profile_id") or "") != profile_id:
        raise RuntimeError("TTS source evidence принадлежит другому profile.")
    if str(source.get("backend_id") or "") != backend_id:
        raise RuntimeError("TTS source evidence принадлежит другому backend.")

    fingerprint = str(profile.fingerprint()).casefold()
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise RuntimeError("TTS profile fingerprint имеет неверный формат.")

    contract_payload = _mapping(contract.as_dict(), "TTS profile contract")
    execution_supported = contract_payload.get("execution_plan_evidence_supported") is True
    execution_required = bool(
        getattr(profile, "requires_execution_plan_evidence", False)
    )
    if execution_required and not execution_supported:
        raise RuntimeError("Default TTS profile требует неподдерживаемую execution evidence.")

    return {
        "policy": OPERATOR_RUNTIME_STATUS_POLICY,
        "runtime": {
            "policy": _safe_id(runtime.get("policy"), "runtime policy"),
            "required_ready": required_ready,
            "feature_count": len(features),
            "state_counts": dict(state_counts),
            "required_failures": sorted(required_failures),
            "optional_degraded": sorted(optional_degraded),
        },
        "tts": {
            "profile_id": profile_id,
            "display_name": _safe_id(
                getattr(profile, "display_name", ""), "profile display_name"
            ),
            "backend_id": backend_id,
            "model_family": _safe_id(
                getattr(profile, "model_family", ""), "model_family"
            ),
            "model_revision": _safe_id(
                getattr(profile, "model_revision", ""), "model_revision"
            ),
            "profile_fingerprint": fingerprint,
            "production_enabled": bool(
                getattr(profile, "production_enabled", False)
            ),
            "registered_profile_count": len(tuple(registered_profiles)),
            "source_kind": _safe_id(source.get("source_kind"), "source_kind"),
            "source_sha256": str(source.get("source_sha256") or ""),
            "manifest_policy": _safe_id(
                source.get("manifest_policy"), "manifest_policy"
            ),
            "adapter_policy": _safe_id(
                getattr(backend, "adapter_policy", ""), "adapter_policy"
            ),
            "profile_contract_policy": _safe_id(
                contract_payload.get("profile_contract_policy"),
                "profile_contract_policy",
            ),
            "execution_plan_required": execution_required,
            "execution_plan_supported": execution_supported,
        },
    }


def operator_runtime_status_html_lines(
    payload: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Format compact HTML lines for the existing admin status message."""
    data = operator_runtime_status_payload() if payload is None else dict(payload)
    runtime = _mapping(data.get("runtime"), "operator runtime status")
    tts = _mapping(data.get("tts"), "operator TTS status")
    counts = _mapping(runtime.get("state_counts"), "runtime state counts")

    required_ready = runtime.get("required_ready") is True
    installed = int(counts.get("installed") or 0)
    feature_count = int(runtime.get("feature_count") or 0)
    optional_degraded = tuple(runtime.get("optional_degraded") or ())
    required_failures = tuple(runtime.get("required_failures") or ())

    runtime_line = (
        f"🧩 Runtime manifest: {'✅ ready' if required_ready else '❌ blocked'} · "
        f"installed={installed}/{feature_count} · "
        f"optional-degraded={len(optional_degraded)}"
    )
    lines = [html.escape(runtime_line)]
    if required_failures:
        lines.append(
            "🚨 Required runtime failures: <code>"
            + html.escape(", ".join(str(value) for value in required_failures))
            + "</code>"
        )
    elif optional_degraded:
        lines.append(
            "⚠️ Optional runtime: <code>"
            + html.escape(", ".join(str(value) for value in optional_degraded))
            + "</code>"
        )

    profile_id = html.escape(str(tts.get("profile_id") or "—"))
    backend_id = html.escape(str(tts.get("backend_id") or "—"))
    revision = html.escape(str(tts.get("model_revision") or "—"))
    profile_count = int(tts.get("registered_profile_count") or 0)
    enabled = tts.get("production_enabled") is True
    lines.append(
        f"🎙 TTS default: {'✅' if enabled else '❌'} "
        f"<code>{profile_id}</code> · backend=<code>{backend_id}</code> · "
        f"revision=<code>{revision}</code> · profiles={profile_count}"
    )

    source_kind = html.escape(str(tts.get("source_kind") or "—"))
    source_sha = _sha_prefix(tts.get("source_sha256"))
    fingerprint = _sha_prefix(tts.get("profile_fingerprint"))
    lines.append(
        "🧾 TTS provenance: "
        f"source=<code>{source_kind}</code> · manifest=<code>{source_sha}</code> · "
        f"profile=<code>{fingerprint}</code>"
    )

    adapter_policy = html.escape(str(tts.get("adapter_policy") or "—"))
    evidence_required = tts.get("execution_plan_required") is True
    evidence_supported = tts.get("execution_plan_supported") is True
    evidence = (
        "required+supported"
        if evidence_required and evidence_supported
        else "supported"
        if evidence_supported
        else "not-supported"
    )
    lines.append(
        f"🛡 TTS adapter: <code>{adapter_policy}</code> · evidence={evidence}"
    )
    return tuple(lines)


def safe_operator_runtime_status_html_lines() -> tuple[str, ...]:
    """Never expose exception messages, which may contain local paths."""
    try:
        return operator_runtime_status_html_lines()
    except Exception as exc:
        return (
            "🧩 Runtime/TTS diagnostics: ❌ <code>"
            + html.escape(type(exc).__name__)
            + "</code>",
        )


class _ReplyCaptureMessage:
    def __init__(self, original: Any) -> None:
        self._original = original
        self.calls: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    async def reply_text(
        self,
        text: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if text is None and "text" in kwargs:
            text = kwargs.pop("text")
        self.calls.append((text, args, dict(kwargs)))
        return None


class _UpdateProxy:
    def __init__(self, original: Any, message: _ReplyCaptureMessage) -> None:
        self._original = original
        self.message = message

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def _extended_status_text(text: object) -> object:
    value = str(text or "")
    if not value.startswith("🩺 <b>Статус бота</b>"):
        return text
    lines = safe_operator_runtime_status_html_lines()
    return value.rstrip() + "\n\n" + "\n".join(lines)


def install_operator_runtime_status(main_module: ModuleType) -> None:
    """Wrap the imported status handler before Telegram handlers are registered."""
    global _INSTALLED
    if _INSTALLED:
        return
    with _LOCK:
        if _INSTALLED:
            return
        original = getattr(main_module, "status_command", None)
        if not callable(original):
            raise RuntimeError("main.status_command отсутствует или не callable.")
        if getattr(original, "_mp3bot_operator_runtime_status", False):
            _INSTALLED = True
            return

        @functools.wraps(original)
        async def status_with_runtime(update: Any, context: Any):
            message = getattr(update, "message", None)
            real_reply = getattr(message, "reply_text", None)
            if not callable(real_reply):
                return await original(update, context)

            captured_message = _ReplyCaptureMessage(message)
            proxied_update = _UpdateProxy(update, captured_message)
            result = await original(proxied_update, context)
            for text, args, kwargs in captured_message.calls:
                await real_reply(_extended_status_text(text), *args, **kwargs)
            return result

        status_with_runtime._mp3bot_operator_runtime_status = True  # type: ignore[attr-defined]
        status_with_runtime._mp3bot_original_status = original  # type: ignore[attr-defined]
        main_module.status_command = status_with_runtime
        _INSTALLED = True


__all__ = [
    "OPERATOR_RUNTIME_STATUS_POLICY",
    "install_operator_runtime_status",
    "operator_runtime_status_html_lines",
    "operator_runtime_status_payload",
    "safe_operator_runtime_status_html_lines",
]
