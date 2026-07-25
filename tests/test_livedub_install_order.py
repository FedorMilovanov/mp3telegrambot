from pathlib import Path


def test_transactional_audio_layers_are_final_before_capturing_wrappers():
    src = Path("bot_new.py").read_text(encoding="utf-8")

    companion = src.index("install_livedub_audio_companion()")
    quality = src.index("install_livedub_audio_quality_guard()")
    new_atomic = src.index("install_livedub_new_delivery_atomicity()")
    cached_atomic = src.index("install_livedub_cached_delivery_atomicity()")
    dedupe = src.index("install_livedub_audio_dedupe()")
    deep = src.index("install_livedub_deep_audit()")

    # Quality guard patches clean-track selection, but also replaces _send_new_audio.
    # Therefore the transactional local sender must install after quality and before
    # dedupe/deep-audit capture the final callable.
    assert companion < quality < new_atomic < cached_atomic < dedupe < deep
