from __future__ import annotations

import json
import sys
from pathlib import Path

from services.dub_preflight import _runtime_plan, _signature
from services.dub_reference_strategies import reference_strategy_for_backend
from services.media_masters import (
    FINAL_MEDIA_VALIDATOR_POLICY,
    MEDIA_MASTER_POLICY,
    MediaMasterRequest,
    get_final_validator,
    get_media_master,
)


def test_reference_strategy_is_selected_by_backend_not_generic_runtime():
    assert reference_strategy_for_backend("voxcpm2").strategy_id == (
        "voxcpm2-extended-composite"
    )
    assert reference_strategy_for_backend("deterministic-ci").strategy_id == (
        "no-reference"
    )


def test_media_master_is_not_owned_by_speech_backend(tmp_path: Path):
    repo = Path(__file__).resolve().parents[1]
    master = get_media_master("constant-mix")
    runtime = master.runtime_paths(
        repo,
        {"translation_mode": "direct", "media_python": sys.executable},
    )
    command = master.build_command(
        runtime,
        MediaMasterRequest(
            source_video=tmp_path / "source.mp4",
            russian_wav=tmp_path / "ru.wav",
            work_dir=tmp_path / "master",
            mixed_video=tmp_path / "mixed.mp4",
            russian_only_video=tmp_path / "ru.mp4",
            original_level=0.18,
        ),
    )
    assert runtime.as_dict()["media_master_policy"] == MEDIA_MASTER_POLICY
    assert "master_monolithic_mix.py" in command[1]
    assert "--source-video" in command
    assert get_final_validator().validator_id == "ffprobe-av-contract"
    assert FINAL_MEDIA_VALIDATOR_POLICY.startswith("backend-neutral")


def test_preflight_plan_consumes_backend_and_media_contracts(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "projects" / "dub-1234567890"
    project_root.mkdir(parents=True)
    request = {
        "schema_version": 1,
        "video_id": "abcdef12345",
        "source_url": "https://youtu.be/abcdef12345",
        "translation_mode": "direct",
        "speech_backend": "deterministic-ci",
        "media_master": "constant-mix",
        "media_python": sys.executable,
        "deterministic_archive": str(tmp_path),
    }
    (project_root / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )

    import services.dub_preflight as preflight

    monkeypatch.setattr(preflight, "studio_root", lambda: tmp_path)
    plan = _runtime_plan(
        {
            "id": "dub-1234567890",
            "work_root": str(project_root),
            "recipe_id": "generic_short_v1",
        }
    )
    signature = _signature(plan)

    assert signature["backend"]["backend_id"] == "deterministic-ci"
    assert signature["speech_runtime"]["backend_id"] == "deterministic-ci"
    assert signature["media_runtime"]["master_id"] == "constant-mix"
    assert "services.media_masters" in signature["modules"]
