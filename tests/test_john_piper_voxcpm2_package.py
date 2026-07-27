from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
)
DIRECT_FILES = (
    EXAMPLE / "Run-John-Piper-FINAL-CPU.ps1",
    EXAMPLE / "voxcpm2_cpu_shorts_production.py",
    EXAMPLE / "master_constant_mix.py",
    EXAMPLE / "segments_ru_final.json",
    EXAMPLE / "subtitles_ru_final.srt",
    EXAMPLE / "source_subtitles_en.srt",
    EXAMPLE / "translation_ru.txt",
)


def test_direct_pipeline_has_no_embedded_runtime_package() -> None:
    assert all(path.is_file() for path in DIRECT_FILES)
    assert not list(EXAMPLE.glob("package.part*.b64"))
    assert not list(EXAMPLE.glob("*.zip"))
    assert not list(EXAMPLE.glob("*-Inner.ps1"))


def test_direct_python_modules_compile() -> None:
    for name in (
        "voxcpm2_cpu_shorts_production.py",
        "master_constant_mix.py",
    ):
        path = EXAMPLE / name
        compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")


def test_john_piper_segments_match_actual_source_duration() -> None:
    segments = json.loads(
        (EXAMPLE / "segments_ru_final.json").read_text(encoding="utf-8-sig")
    )
    assert len(segments) == 5
    assert abs(float(segments[0]["start"]) - 0.233) < 0.001
    assert abs(float(segments[-1]["end"]) - 62.514) < 0.001
    assert [item["start_delay_ms"] for item in segments] == [220, 160, 100, 70, 40]

    previous_end = 0.0
    for item in segments:
        start = float(item["start"])
        end = float(item["end"])
        assert start >= previous_end - 0.001
        assert end > start
        assert end <= 62.514 + 0.001
        assert item["text"].strip()
        previous_end = end

    combined = " ".join(item["text"] for item in segments)
    assert "самым радикальным человеком в мире" in combined
    assert "Советник, Которому вы доверяете" in combined
    assert "Своей смертью и воскресением" in combined


def test_subtitle_bounds_do_not_exceed_video_duration() -> None:
    pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
    for name in ("subtitles_ru_final.srt", "source_subtitles_en.srt"):
        values: list[float] = []
        text = (EXAMPLE / name).read_text(encoding="utf-8-sig")
        for match in pattern.finditer(text):
            hours, minutes, seconds, millis = map(int, match.groups())
            values.append(hours * 3600 + minutes * 60 + seconds + millis / 1000)
        assert values
        assert max(values) <= 62.514 + 0.001


def test_synthesis_supports_resumable_checkpoints() -> None:
    source = (EXAMPLE / "voxcpm2_cpu_shorts_production.py").read_text(
        encoding="utf-8-sig"
    )
    assert 'work_dir / "checkpoints"' in source
    assert "checkpoint_path.is_file()" in source
    assert "повторный CPU-синтез не нужен" in source
    assert '"--force-segments"' in source

    launcher = (EXAMPLE / "Run-John-Piper-FINAL-CPU.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert 'Join-Path $SegmentWorkDir "attempts"' in launcher
    assert 'Join-Path $SegmentWorkDir "segments_fitted"' not in launcher.split(
        'if (-not $KeepDiagnostics) {'
    )[-1]
