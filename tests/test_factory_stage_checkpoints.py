from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from services import shorts_factory_stage_checkpoint as stage
from services.shorts_factory_retry_cache import cache_ttl_seconds


def _payload(*, verified: bool = False) -> dict[str, Any]:
    return {
        "metadata": {
            "language": "ru",
            "format": "speech",
            "title_ru": "Test",
            "author_ru": "Author",
        },
        "shorts_candidates": [
            {
                "start_seconds": 5.0,
                "end_seconds": 50.0,
                "title_ru": "Self-contained thought",
                "reason_ru": "Verified source quote",
                "quality_score": 94.0,
                "boundary_verified": verified,
            }
        ],
        "long_candidates": [],
    }


def _store(tmp_path: Path) -> stage.StageCheckpointStore:
    return stage.StageCheckpointStore(
        audio_sha256="a" * 64,
        duration=120,
        model="gemini-3.8-flash",
        schema_sha256="s" * 64,
        dependency_sha256="d" * 64,
        directory=tmp_path,
    )


def test_immutable_identity_bound_checkpoint_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    payload = _payload(verified=False)
    store.save("scout", "exact scout prompt", payload)
    assert store.load("scout", "exact scout prompt") == payload
    assert store.load("scout", "changed prompt") is None
    assert store.load("judge", "exact scout prompt") is None
    assert replace(store, model="gemini-other").load("scout", "exact scout prompt") is None
    assert replace(store, audio_sha256="b" * 64).load("scout", "exact scout prompt") is None
    assert replace(store, schema_sha256="x" * 64).load("scout", "exact scout prompt") is None
    assert replace(store, dependency_sha256="z" * 64).load("scout", "exact scout prompt") is None
    assert replace(store, duration=119).load("scout", "exact scout prompt") is None


def test_tampered_and_truncated_checkpoint_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("judge", "exact", _payload())
    path = store._path("judge", "exact")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["shorts_candidates"][0]["title_ru"] = "TAMPERED"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert store.load("judge", "exact") is None
    path.write_bytes(b"{partial")
    assert store.load("judge", "exact") is None


def test_stale_and_future_checkpoint_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("scout", "exact", _payload())
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + cache_ttl_seconds() + 1)
    assert store.load("scout", "exact") is None
    monkeypatch.setattr(time, "time", lambda: now - 30)
    assert store.load("scout", "exact") is None


def test_malformed_model_result_never_persisted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("scout", "bad", {"ok": True})
    assert not list(tmp_path.glob("*.json"))
    assert store.load("scout", "bad") is None


def test_final_audit_requires_verified_boundaries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.save("audit", "prompt", _payload(verified=False))
    # The on-disk stage cache must not promote provisional boundaries to verified.
    assert store.load("audit", "prompt") is None
    store.save("audit", "prompt", _payload(verified=True))
    assert store.load("audit", "prompt") == _payload(verified=True)


def test_checkpoint_handles_oversize_and_bounded_retention(tmp_path: Path) -> None:
    store = _store(tmp_path)
    data = _payload()
    data["metadata"]["analysis_note"] = "x" * (stage._MAX_CHECKPOINT_BYTES + 1)
    store.save("scout", "huge", data)
    assert store.load("scout", "huge") is None

    for i in range(30):
        store.save("scout", f"prompt-{i}", _payload())
    assert len(list(tmp_path.glob("*.json"))) <= stage._MAX_CHECKPOINT_FILES


def test_real_file_fingerprint_changes_with_audio_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage, "STAGE_CHECKPOINT_DIR", tmp_path / "checkpoints")
    audio = tmp_path / "audio.aac"
    audio.write_bytes(b"real-audio-part-A")
    schema = {"type": "object", "required": ["metadata"]}

    first = asyncio.run(
        stage.StageCheckpointStore.from_audio(audio, duration=120, model="gemini-3.8-flash", schema=schema)
    )
    audio.write_bytes(b"real-audio-part-B")
    second = asyncio.run(
        stage.StageCheckpointStore.from_audio(audio, duration=120, model="gemini-3.8-flash", schema=schema)
    )
    assert first.audio_sha256 != second.audio_sha256
    assert first.dependency_sha256 == second.dependency_sha256


def test_schema_thinking_mode_change_invalidates_checkpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = _store(tmp_path)
    monkeypatch.setenv("GEMINI_SCHEMA_THINKING", "1")
    store.save("scout", "prompt", _payload())
    assert store.load("scout", "prompt") == _payload()
    monkeypatch.setenv("GEMINI_SCHEMA_THINKING", "0")
    assert store.load("scout", "prompt") is None
