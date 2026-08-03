from __future__ import annotations

from pathlib import Path

from tools.voxcpm2.repair_project_request import canonicalize_request


def test_canonicalize_removes_conflicting_flat_tts_keys() -> None:
    source = {
        "schema_version": 1,
        "video_id": "abc12345",
        "source_url": "https://youtu.be/abc12345",
        "translation_mode": "direct",
        "steps": 16,
        "threads": 4,
        "cfg": 1.9,
        "cpu_venv": "C:/wrong-venv",
        "vox_archive": "C:/wrong-model",
        "speech_profile_fingerprint": "stale",
        "speech_options": {
            "threads": 6,
            "steps": 18,
            "cfg": 1.7,
            "cache_length": 2048,
            "base_seed": 1,
        },
    }

    result = canonicalize_request(
        source,
        threads=10,
        steps=22,
        cfg=1.8,
        cache_length=4096,
        base_seed=2026080322,
        cpu_venv=Path(r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv"),
        vox_archive=Path(r"C:\AI-Archive\VoxCPM2-paused-RTX3060"),
        original_level=0.18,
        russian_delay_ms=520,
    )

    for key in (
        "threads",
        "steps",
        "cfg",
        "cache_length",
        "base_seed",
        "cpu_venv",
        "vox_archive",
        "speech_profile_fingerprint",
    ):
        assert key not in result

    assert result["speech_options"] == {
        "threads": 10,
        "steps": 22,
        "cfg": 1.8,
        "cache_length": 4096,
        "base_seed": 2026080322,
    }
    assert result["speech_backend_config"] == {
        "vox_archive": r"C:\AI-Archive\VoxCPM2-paused-RTX3060",
        "cpu_venv": r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
    }
    assert result["speech_backend"] == "voxcpm2"
    assert result["speech_model_profile"] == "voxcpm2-production-v1"
    assert result["original_level"] == 0.18
    assert result["russian_delay_ms"] == 520


def test_canonicalize_preserves_unrelated_project_fields() -> None:
    source = {
        "schema_version": 1,
        "video_id": "abc12345",
        "source_url": "https://youtu.be/abc12345",
        "translation_mode": "direct",
        "title_model": "gemini-test",
        "custom_metadata": {"keep": True},
    }

    result = canonicalize_request(
        source,
        threads=10,
        steps=22,
        cfg=1.8,
        cache_length=4096,
        base_seed=123,
        cpu_venv=Path("cpu-venv"),
        vox_archive=Path("archive"),
        original_level=0.18,
        russian_delay_ms=520,
    )

    assert result["title_model"] == "gemini-test"
    assert result["custom_metadata"] == {"keep": True}
    assert source.get("speech_options") is None
