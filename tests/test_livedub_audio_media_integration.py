from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from services import livedub_audio_companion as companion
from services.livedub_audio_quality_guard import select_clean_translation_mp3


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg/FFprobe are required for the media integration test",
)


def _run(command: list[str]) -> None:
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_clean_ru_and_final_mix_are_valid_distinct_mp3_files(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None

    video = tmp_path / "publication.mp4"
    clean = tmp_path / "translation.live.mp3"

    _run([
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1.5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
        "-shortest", "-c:v", "mpeg4", "-q:v", "5",
        "-c:a", "aac", "-b:a", "128k", str(video),
    ])
    _run([
        ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1.5",
        "-c:a", "libmp3lame", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        str(clean),
    ])

    mixed = companion._extract_mix_mp3(video)

    clean_ok, clean_duration = companion._probe_audio(clean)
    mixed_ok, mixed_duration = companion._probe_audio(mixed)
    video_ok, video_duration = companion._probe_audio(video)

    assert video_ok and clean_ok and mixed_ok
    assert abs(clean_duration - video_duration) <= 1
    assert abs(mixed_duration - video_duration) <= 1
    assert clean.resolve() != mixed.resolve()
    assert _sha256(clean) != _sha256(mixed)
    assert select_clean_translation_mp3(tmp_path) == clean
