from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq"
PRODUCTION = ROOT / "tools" / "voxcpm2" / "production"

def test_direct_pipeline_has_no_embedded_package() -> None:
    assert not list(EXAMPLE.glob("package.part*.b64"))
    assert not (EXAMPLE / "John_Piper_VoxCPM2_CPU_FINAL.zip").exists()
    assert not (EXAMPLE / "Run-John-Piper-FINAL-CPU-Inner.ps1").exists()

def test_direct_python_modules_compile() -> None:
    for path in (
        PRODUCTION / "segmented_voice_clone.py",
        PRODUCTION / "master_constant_mix.py",
    ):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

def test_john_piper_recipe_and_segments() -> None:
    project = json.loads((EXAMPLE / "project.json").read_text(encoding="utf-8"))
    assert project["engine"] == "VoxCPM2"
    assert project["device"] == "cpu"
    segments = json.loads((EXAMPLE / "segments_ru_final.json").read_text(encoding="utf-8"))
    assert len(segments) == 5
    assert segments[0]["start"] == 0.24
    assert segments[-1]["end"] == 64.32
    assert [item["start_delay_ms"] for item in segments] == [220, 160, 100, 70, 40]
    previous_end = 0.0
    for segment in segments:
        assert segment["start"] >= previous_end
        assert segment["end"] > segment["start"]
        assert segment["text"].strip()
        previous_end = segment["end"]
