from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.voxcpm2.examples.john_piper_z20py4yqhyq import (
    voxcpm2_cpu_shorts_production as direct_wrapper,
)


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


def test_synthesis_supports_transactional_resumable_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compatibility = {
        "schema_version": 2,
        "policy": direct_wrapper.MARKER_POLICY,
        "speech_backend": "voxcpm2",
        "render_contract_sha256": "a" * 64,
        "cache_length": 4096,
        "python_executable": "python",
    }
    checkpoint = tmp_path / "checkpoints" / "segment_01.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text('{"complete": true}', encoding="utf-8")
    monkeypatch.setattr(
        direct_wrapper,
        "_runtime_contract",
        lambda: (tmp_path, dict(compatibility)),
    )

    assert direct_wrapper.run(lambda: "rendered") == "rendered"
    marker = json.loads(
        (tmp_path / "direct_cli_runtime.marker.json").read_text(encoding="utf-8")
    )
    completed = json.loads(
        (tmp_path / "direct_cli_runtime.completed.json").read_text(encoding="utf-8")
    )
    assert marker == compatibility
    assert completed["compatibility"] == compatibility
    assert checkpoint.is_file()

    def fail() -> None:
        raise RuntimeError("render failed")

    with pytest.raises(RuntimeError, match="render failed"):
        direct_wrapper.run(fail)
    assert checkpoint.is_file()
    assert (tmp_path / "direct_cli_runtime.marker.json").is_file()
    assert not (tmp_path / "direct_cli_runtime.completed.json").exists()
    failure = json.loads(
        (tmp_path / "direct_renderer_failure.json").read_text(encoding="utf-8")
    )
    assert failure["error_type"] == "RuntimeError"
    assert failure["message"] == "render failed"

    launcher = (EXAMPLE / "Run-John-Piper-FINAL-CPU.ps1").read_text(
        encoding="utf-8-sig"
    )
    assert 'Join-Path $SegmentWorkDir "attempts"' in launcher
    assert 'Join-Path $SegmentWorkDir "segments_fitted"' not in launcher.split(
        'if (-not $KeepDiagnostics) {'
    )[-1]
