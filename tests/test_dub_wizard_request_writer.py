from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from handlers import dub_wizard
from services.speech_backends import DEFAULT_MODEL_PROFILE_ID, default_model_profile


def _payload(**overrides):
    return {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "gemini",
        "speech_backend": "voxcpm2",
        "speech_model_profile": DEFAULT_MODEL_PROFILE_ID,
        "media_master": "constant-mix",
        "final_media_validator": "ffprobe-av-contract",
        **overrides,
    }


def test_wizard_writes_validated_request_inside_project_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        dub_wizard.generic_project_runtime._legacy,
        "studio_root",
        lambda: tmp_path / "studio",
    )
    path = dub_wizard._write_request("dub-0123456789", _payload())
    expected = (
        tmp_path
        / "studio"
        / "projects"
        / "dub-0123456789"
        / "request.json"
    ).resolve()
    saved = json.loads(path.read_text(encoding="utf-8"))
    profile = default_model_profile()

    assert path == expected
    assert saved["video_id"] == "AbCdEf12345"
    assert saved["speech_backend"] == "voxcpm2"
    assert saved["speech_model_profile"] == DEFAULT_MODEL_PROFILE_ID
    assert saved["speech_profile_fingerprint"] == profile.fingerprint()
    assert saved["speech_options"] == {
        spec.name: spec.default for spec in profile.option_specs
    }
    assert saved["speech_backend_config"] == dict(profile.backend_defaults)
    assert not list(path.parent.glob("request.json.tmp.*"))


@pytest.mark.parametrize(
    "payload",
    [
        _payload(schema_version=True),
        _payload(schema_version=1.5),
        _payload(source_url="https://youtube.com/watch?v=OtherId999"),
        _payload(source_url="https://youtube.com/playlist?list=PL123"),
        _payload(translation_mode="unknown"),
        _payload(speech_backend="future-neural-engine"),
        _payload(speech_model_profile="future-neural-model"),
        _payload(speech_options={"temperature": 0.7}),
    ],
)
def test_invalid_wizard_request_is_not_written(
    monkeypatch,
    tmp_path: Path,
    payload: dict,
) -> None:
    monkeypatch.setattr(
        dub_wizard.generic_project_runtime._legacy,
        "studio_root",
        lambda: tmp_path / "studio",
    )
    with pytest.raises(RuntimeError):
        dub_wizard._write_request("dub-0123456789", payload)
    request_path = (
        tmp_path
        / "studio"
        / "projects"
        / "dub-0123456789"
        / "request.json"
    )
    assert not request_path.exists()
    assert not list(request_path.parent.glob("request.json.tmp.*"))


def test_wizard_builds_generic_json_options_and_legacy_overrides(monkeypatch) -> None:
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", '{"steps":22,"cfg":1.92}')
    monkeypatch.setenv("DUB_VOX_THREADS", "7")
    monkeypatch.setenv(
        "DUB_TTS_BACKEND_CONFIG_JSON",
        '{"vox_archive":"C:/models/vox-next"}',
    )
    monkeypatch.setenv("DUB_CPU_VENV", "C:/venvs/vox-next")

    payload = dub_wizard._request_payload(
        "AbCdEf12345",
        "https://youtube.com/watch?v=AbCdEf12345",
        "gemini",
    )

    assert payload["speech_model_profile"] == DEFAULT_MODEL_PROFILE_ID
    assert payload["speech_options"] == {"steps": 22, "cfg": 1.92, "threads": 7}
    assert payload["speech_backend_config"] == {
        "vox_archive": "C:/models/vox-next",
        "cpu_venv": "C:/venvs/vox-next",
    }
    assert "vox_archive" not in payload
    assert "threads" not in payload


def test_wizard_rejects_non_object_tts_env(monkeypatch) -> None:
    monkeypatch.setenv("DUB_TTS_OPTIONS_JSON", "[1, 2, 3]")
    with pytest.raises(RuntimeError, match="JSON-объект"):
        dub_wizard._request_payload(
            "AbCdEf12345",
            "https://youtube.com/watch?v=AbCdEf12345",
            "gemini",
        )


class _FakeMessage:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def reply_text(self, *_args: Any, **_kwargs: Any) -> None:
        self.order.append("reply")


class _FakeStore:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    def create_project(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.order.append("create")
        return {"id": "dub-0123456789"}

    def enqueue_job(self, project_id: str, action: str) -> dict[str, Any]:
        self.order.append(f"enqueue:{project_id}:{action}")
        return {"id": 91}


def _update(order: list[str]) -> Any:
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=456),
        effective_message=_FakeMessage(order),
    )


def test_gemini_job_is_enqueued_only_after_durable_request(
    monkeypatch,
    tmp_path: Path,
) -> None:
    order: list[str] = []
    store = _FakeStore(order)
    monkeypatch.setattr(dub_wizard._legacy, "DubStore", lambda: store)
    monkeypatch.setattr(
        dub_wizard,
        "_extract_youtube_video_id",
        lambda _url: ("AbCdEf12345", "https://youtube.com/watch?v=AbCdEf12345"),
    )
    monkeypatch.setattr(
        dub_wizard._legacy,
        "_request_payload",
        lambda video_id, source_url, mode: _payload(
            video_id=video_id,
            source_url=source_url,
            translation_mode=mode,
        ),
    )

    request_path = tmp_path / "request.json"

    def write_request(project_id: str, payload: dict[str, Any]) -> Path:
        assert project_id == "dub-0123456789"
        request_path.write_text(json.dumps(payload), encoding="utf-8")
        order.append("request")
        return request_path

    monkeypatch.setattr(dub_wizard, "_write_request", write_request)
    context = SimpleNamespace(
        user_data={dub_wizard._legacy._WIZARD_KEY: {"awaiting": "url"}}
    )

    asyncio.run(
        dub_wizard._create_generic_project(
            _update(order),
            context,
            "https://youtu.be/AbCdEf12345",
            dub_wizard._legacy._GEMINI_MODE,
        )
    )

    assert order == [
        "create",
        "request",
        "enqueue:dub-0123456789:render_gemini",
        "reply",
    ]
    assert request_path.is_file()
    assert context.user_data == {}


def test_failed_request_write_never_enqueues_job(
    monkeypatch,
) -> None:
    order: list[str] = []
    store = _FakeStore(order)
    monkeypatch.setattr(dub_wizard._legacy, "DubStore", lambda: store)
    monkeypatch.setattr(
        dub_wizard,
        "_extract_youtube_video_id",
        lambda _url: ("AbCdEf12345", "https://youtube.com/watch?v=AbCdEf12345"),
    )
    monkeypatch.setattr(
        dub_wizard._legacy,
        "_request_payload",
        lambda *_args: _payload(),
    )

    def fail_write(*_args: Any, **_kwargs: Any) -> Path:
        order.append("request-failed")
        raise OSError("DISK_SENTINEL")

    monkeypatch.setattr(dub_wizard, "_write_request", fail_write)

    with pytest.raises(OSError, match="DISK_SENTINEL"):
        asyncio.run(
            dub_wizard._create_generic_project(
                _update(order),
                SimpleNamespace(user_data={}),
                "https://youtu.be/AbCdEf12345",
                dub_wizard._legacy._GEMINI_MODE,
            )
        )

    assert order == ["create", "request-failed"]
    assert not any(item.startswith("enqueue:") for item in order)


def test_wizard_facade_patches_legacy_request_and_creation_hooks() -> None:
    assert Path(dub_wizard.__file__).name == "__init__.py"
    assert dub_wizard._legacy._request_payload is dub_wizard._request_payload
    assert dub_wizard._legacy._write_request is dub_wizard._write_request
    assert dub_wizard._legacy._create_generic_project is dub_wizard._create_generic_project
    source = Path(dub_wizard.__file__).read_text(encoding="utf-8")
    assert "generic_project_runtime.validate_request_payload(payload)" in source
    assert "generic_project_runtime.save_json(destination, validated)" in source
