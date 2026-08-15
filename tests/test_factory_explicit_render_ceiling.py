from pathlib import Path

from services.clip_renderer import clip_snap_ceiling


def test_factory_long_ceiling_is_absolute_start_plus_public_limit():
    assert clip_snap_ceiling(250.0, 900.0, 2000.0) == 1150.0
    assert clip_snap_ceiling(250.0, 900.0, 1000.0) == 1000.0


def test_factory_long_render_passes_public_cap_explicitly():
    factory = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    clips = Path("pipelines/clips.py").read_text(encoding="utf-8")
    renderer = Path("services/clip_renderer.py").read_text(encoding="utf-8")

    assert "public_max_seconds=FACTORY_LONG_PUBLIC_MAX_SEC" in factory
    assert "FACTORY_LONG_PUBLIC_MAX_SEC" in factory
    assert "silence_snap_max_end=snap_ceiling" in clips
    assert "ceiling = start + maximum" in renderer
    assert "adjusted = min(adjusted, ceiling)" in renderer
    assert "end = min(end, ceiling)" in renderer
