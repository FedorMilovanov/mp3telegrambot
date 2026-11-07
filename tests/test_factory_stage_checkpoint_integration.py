from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services import shorts_factory_capacity as capacity
from services import shorts_factory_capacity_runtime as runtime
from services import shorts_factory_stage_checkpoint as stage


def _plan(boundary_verified: bool) -> dict[str, Any]:
    return {
        "metadata": {
            "language": "ru",
            "format": "sermon",
            "title_ru": "Speech",
            "author_ru": "Speaker",
        },
        "shorts_candidates": [
            {
                "start_seconds": 10.0,
                "end_seconds": 90.0,
                "title_ru": "Complete quotation",
                "reason_ru": "Coherent argument",
                "quality_score": 91.0,
                "boundary_verified": boundary_verified,
            }
        ],
        "long_candidates": [],
    }


class Client:
    def __init__(self) -> None:
        self.aio = SimpleNamespace(aclose=self.aclose)
        self.closes = 0

    async def aclose(self) -> None:
        self.closes += 1

    def close(self) -> None:
        self.closes += 1


def test_completed_passes_survive_process_like_new_run_without_new_api_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as quality_gate
    import services.shorts_factory_source as source

    monkeypatch.setattr(stage, "STAGE_CHECKPOINT_DIR", tmp_path / "stage")
    audio = tmp_path / "analysis.aac"
    audio.write_bytes(b"sample" * 2048)
    clients: list[Client] = []
    calls: list[str] = []

    def new_clients() -> list[Client]:
        client = Client()
        clients.append(client)
        return [client]

    async def verified_duration(path: Path) -> float:
        return 120.0

    async def run_pass(client: Any, **kwargs: Any) -> dict[str, Any]:
        prompt = kwargs["prompt"]
        which = "scout" if prompt == "scout" else "judge" if prompt == "judge" else "audit"
        calls.append(which)
        return copy.deepcopy(_plan(boundary_verified=(which == "audit")))

    monkeypatch.setattr(capacity, "factory_gemini_clients", new_clients)
    monkeypatch.setattr(capacity, "factory_gemini_quota_domains", lambda: [""])
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.8-flash")
    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "audit")
    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            Part=SimpleNamespace(from_bytes=lambda **kwargs: SimpleNamespace(**kwargs)),
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    monkeypatch.setattr(
        candidates,
        "validate_factory_plan",
        lambda raw, duration, require_verified=True: {
            "metadata": copy.deepcopy(raw["metadata"]),
            "shorts_candidates": [{"start": 10.0, "end": 90.0}]
            if raw["shorts_candidates"][0]["boundary_verified"]
            else [],
            "long_candidates": [],
        },
    )
    monkeypatch.setattr(quality_gate, "apply_factory_quality_gate", lambda plan: plan)
    monkeypatch.setattr(quality_gate, "validated_factory_plan_language", lambda plan: "ru")
    monkeypatch.setattr(source, "measure_factory_audio_duration", verified_duration)
    monkeypatch.setattr(source, "factory_duration_matches", lambda *args: True)
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda _: "audio/aac")

    def run() -> dict[str, Any]:
        return asyncio.run(runtime.create_factory_plan_resumable(audio, title="T", performer="P", duration=120))

    first = run()
    assert first["review_passes"] == 3
    assert calls == ["scout", "judge", "audit"]
    assert len(clients) == 1
    assert clients[0].closes == 2

    # New runtime invocation loads all three validated checkpoints. No client
    # construction, file upload, inference, or publication stage is reached.
    second = run()
    assert second == first
    assert calls == ["scout", "judge", "audit"]
    assert len(clients) == 1

    # Discard the final audit only. Scout and judge must be reused; only the
    # strict boundary verification is run again.
    for file in (tmp_path / "stage").glob("*.json"):
        record = json.loads(file.read_text(encoding="utf-8"))
        if record["identity"]["stage"] == "audit":
            file.unlink()
    third = run()
    assert third == first
    assert calls == ["scout", "judge", "audit", "audit"]
    assert len(clients) == 2
    assert clients[1].closes == 2

    # Same metadata with different decoded audio invalidates every stage.
    audio.write_bytes(b"different" * 2048)
    fourth = run()
    assert fourth == first
    assert calls[-3:] == ["scout", "judge", "audit"]
    assert len(clients) == 3
