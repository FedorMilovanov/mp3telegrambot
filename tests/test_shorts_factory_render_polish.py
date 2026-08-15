from pathlib import Path


def test_factory_short_normalization_is_mandatory_and_fail_closed():
    delivery = Path("pipelines/factory_short_delivery.py").read_text(encoding="utf-8")

    normalize = delivery.index("normalized = await postprocess_short(")
    reject = delivery.index("if not normalized:", normalize)
    transcript = delivery.index("segments = await transcribe_short_clip(", reject)

    assert normalize < reject < transcript
    assert "mandatory audio normalization failed" in delivery
    assert "current_path = post_path" in delivery
    assert "current_path = raw_path" not in delivery[normalize:transcript]


def test_factory_short_uses_exact_audited_end_without_second_silence_snap():
    delivery = Path("pipelines/factory_short_delivery.py").read_text(encoding="utf-8")
    renderer = Path("services/shorts_video.py").read_text(encoding="utf-8")

    assert "silence_snap_max_end=ceiling" in delivery
    assert "snap_to_silence=False" in delivery
    assert "if snap_to_silence:" in renderer
    assert "if hard_ceiling is not None:" in renderer


def test_factory_long_uses_public_900_cap_and_exact_interval_owner():
    factory = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    clips = Path("pipelines/clips.py").read_text(encoding="utf-8")
    renderer = Path("services/clip_renderer.py").read_text(encoding="utf-8")

    assert "FACTORY_LONG_PUBLIC_MAX_SEC = 900.0" in factory
    assert "public_max_seconds=FACTORY_LONG_PUBLIC_MAX_SEC" in factory
    assert "snap_to_silence=False" in factory
    assert "snap_to_silence=snap_to_silence" in clips
    assert "if snap_to_silence:" in renderer


def test_deleted_render_polish_installer_cannot_return():
    assert not Path("services/shorts_factory_render_polish.py").exists()
