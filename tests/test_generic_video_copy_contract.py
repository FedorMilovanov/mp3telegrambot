from pathlib import Path


def test_generic_packet_copy_requires_machine_level_unity_speed():
    source = Path("services/shorts_video.py").read_text(encoding="utf-8")
    helper_start = source.index("def _normalize_only_can_copy_video")
    helper_end = source.index("async def _normalize_audio_copy_video", helper_start)
    helper = source[helper_start:helper_end]

    assert "abs(value - 1.0) <= 1e-9" in helper
    assert "<= 0.01" not in helper
