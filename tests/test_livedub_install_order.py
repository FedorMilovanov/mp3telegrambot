from pathlib import Path


def test_transactional_audio_layers_install_before_capturing_wrappers():
    src = Path("bot_new.py").read_text(encoding="utf-8")

    companion = src.index("install_livedub_audio_companion()")
    new_atomic = src.index("install_livedub_new_delivery_atomicity()")
    cached_atomic = src.index("install_livedub_cached_delivery_atomicity()")
    quality = src.index("install_livedub_audio_quality_guard()")
    dedupe = src.index("install_livedub_audio_dedupe()")
    deep = src.index("install_livedub_deep_audit()")

    assert companion < new_atomic < cached_atomic < quality < dedupe < deep
