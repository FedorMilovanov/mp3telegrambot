from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from services import operator_runtime_status as status


class _Contract:
    def __init__(self, *, supported: bool = True) -> None:
        self.supported = supported
        self.validated = 0

    def validate_profile(self, profile) -> None:
        self.validated += 1
        assert profile.profile_id == "voxcpm2-production-v1"

    def as_dict(self) -> dict:
        return {
            "backend_id": "voxcpm2",
            "option_keys": ["threads"],
            "required_option_keys": ["threads"],
            "backend_config_keys": ["archive_root"],
            "required_backend_config_keys": ["archive_root"],
            "execution_plan_evidence_supported": self.supported,
            "profile_contract_policy": "speech-backend-model-profile-contract-v1",
        }


class _Message:
    def __init__(self) -> None:
        self.replies: list[tuple[object, tuple, dict]] = []

    async def reply_text(self, text=None, *args, **kwargs):
        self.replies.append((text, args, kwargs))
        return SimpleNamespace(message_id=len(self.replies))


class _Update:
    def __init__(self, user_id: int = 1) -> None:
        self.effective_user = SimpleNamespace(id=user_id)
        self.message = _Message()


def _profile(*, evidence: bool = True):
    return SimpleNamespace(
        profile_id="voxcpm2-production-v1",
        backend_id="voxcpm2",
        display_name="VoxCPM2 production",
        model_family="voxcpm2",
        model_revision="local-archive-pinned-v1",
        production_enabled=True,
        requires_execution_plan_evidence=evidence,
        fingerprint=lambda: "a" * 64,
        backend_defaults={"archive_root": "C:/private/model"},
    )


def _runtime_payload(*, ready: bool = True) -> dict:
    return {
        "policy": "declarative-runtime-composition-v1",
        "required_ready": ready,
        "features": {
            "singleton": {
                "required": True,
                "state": "installed" if ready else "failed",
                "phase": "pre-main",
                "detail": "C:/private/bot.lock",
            },
            "optional-debug": {
                "required": False,
                "state": "skipped",
                "phase": "post-main",
                "detail": "D:/private/config.json",
            },
        },
    }


def _source() -> dict:
    return {
        "schema_version": 1,
        "profile_id": "voxcpm2-production-v1",
        "backend_id": "voxcpm2",
        "model_revision": "local-archive-pinned-v1",
        "source": "voxcpm2-production-v1.json",
        "source_kind": "repository-manifest",
        "source_sha256": "b" * 64,
        "manifest_policy": "strict-tts-profile-manifest-v1",
    }


def test_payload_aggregates_manifest_and_tts_without_private_details() -> None:
    contract = _Contract()
    payload = status.operator_runtime_status_payload(
        runtime_payload=_runtime_payload(),
        profile=_profile(),
        backend=SimpleNamespace(
            backend_id="voxcpm2",
            adapter_policy="audited-voxcpm2-generation-call-v1",
            model_path="C:/private/model",
        ),
        source_evidence=_source(),
        registered_profiles=(_profile(),),
        contract=contract,
    )
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    assert payload["policy"] == status.OPERATOR_RUNTIME_STATUS_POLICY
    assert payload["runtime"]["required_ready"] is True
    assert payload["runtime"]["state_counts"] == {
        "installed": 1,
        "pending": 0,
        "failed": 0,
        "skipped": 1,
    }
    assert payload["runtime"]["optional_degraded"] == ["optional-debug"]
    assert payload["tts"]["profile_id"] == "voxcpm2-production-v1"
    assert payload["tts"]["registered_profile_count"] == 1
    assert payload["tts"]["execution_plan_required"] is True
    assert payload["tts"]["execution_plan_supported"] is True
    assert contract.validated == 1
    assert "C:/private" not in serialized
    assert "D:/private" not in serialized
    assert "detail" not in serialized
    assert "backend_defaults" not in serialized
    assert "model_path" not in serialized


def test_payload_rejects_manifest_readiness_inconsistency() -> None:
    runtime = _runtime_payload(ready=False)
    runtime["required_ready"] = True
    with pytest.raises(RuntimeError, match="required_ready"):
        status.operator_runtime_status_payload(
            runtime_payload=runtime,
            profile=_profile(),
            backend=SimpleNamespace(
                backend_id="voxcpm2",
                adapter_policy="audited-voxcpm2-generation-call-v1",
            ),
            source_evidence=_source(),
            registered_profiles=(_profile(),),
            contract=_Contract(),
        )


def test_html_formatter_labels_default_profile_and_truncates_hashes() -> None:
    payload = status.operator_runtime_status_payload(
        runtime_payload=_runtime_payload(),
        profile=_profile(),
        backend=SimpleNamespace(
            backend_id="voxcpm2",
            adapter_policy="audited-voxcpm2-generation-call-v1",
        ),
        source_evidence=_source(),
        registered_profiles=(_profile(),),
        contract=_Contract(),
    )
    rendered = "\n".join(status.operator_runtime_status_html_lines(payload))

    assert "Runtime manifest: ✅ ready" in rendered
    assert "optional-degraded=1" in rendered
    assert "TTS default" in rendered
    assert "voxcpm2-production-v1" in rendered
    assert "bbbbbbbbbbbb" in rendered
    assert "aaaaaaaaaaaa" in rendered
    assert "required+supported" in rendered
    assert "C:/private" not in rendered


def test_status_wrapper_appends_to_same_admin_reply(monkeypatch) -> None:
    monkeypatch.setattr(status, "_INSTALLED", False)
    monkeypatch.setattr(
        status,
        "safe_operator_runtime_status_html_lines",
        lambda: ("🧩 Runtime fixture", "🎙 TTS fixture"),
    )

    async def original(update, context):
        del context
        await update.message.reply_text(
            "🩺 <b>Статус бота</b>\n\n🔧 ✅ffmpeg",
            parse_mode="HTML",
        )
        return "ok"

    main = SimpleNamespace(status_command=original)
    update = _Update()
    status.install_operator_runtime_status(main)
    wrapped = main.status_command

    assert asyncio.run(wrapped(update, SimpleNamespace())) == "ok"
    assert len(update.message.replies) == 1
    text, _args, kwargs = update.message.replies[0]
    assert text.count("🩺 <b>Статус бота</b>") == 1
    assert "🧩 Runtime fixture" in text
    assert "🎙 TTS fixture" in text
    assert kwargs["parse_mode"] == "HTML"
    assert getattr(wrapped, "_mp3bot_operator_runtime_status") is True

    status.install_operator_runtime_status(main)
    assert main.status_command is wrapped


def test_status_wrapper_preserves_non_status_reply(monkeypatch) -> None:
    monkeypatch.setattr(status, "_INSTALLED", False)

    async def original(update, context):
        del context
        await update.message.reply_text("⛔ Нет доступа", parse_mode="HTML")

    main = SimpleNamespace(status_command=original)
    update = _Update(user_id=999)
    status.install_operator_runtime_status(main)
    asyncio.run(main.status_command(update, SimpleNamespace()))

    assert len(update.message.replies) == 1
    assert update.message.replies[0][0] == "⛔ Нет доступа"


def test_safe_formatter_never_exposes_exception_message(monkeypatch) -> None:
    monkeypatch.setattr(
        status,
        "operator_runtime_status_html_lines",
        lambda: (_ for _ in ()).throw(RuntimeError("C:/private/model/config.json")),
    )
    rendered = "\n".join(status.safe_operator_runtime_status_html_lines())

    assert "RuntimeError" in rendered
    assert "C:/private" not in rendered
    assert "config.json" not in rendered
