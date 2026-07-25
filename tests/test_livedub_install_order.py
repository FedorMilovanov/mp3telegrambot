from pathlib import Path


def test_transactional_audio_layers_are_final_before_capturing_wrappers():
    src = Path("bot_new.py").read_text(encoding="utf-8")

    companion = src.index("install_livedub_audio_companion()")
    quality = src.index("install_livedub_audio_quality_guard()")
    provenance = src.index("install_livedub_ru_provenance()")
    new_atomic = src.index("install_livedub_new_delivery_atomicity()")
    cached_atomic = src.index("install_livedub_cached_delivery_atomicity()")
    dedupe = src.index("install_livedub_audio_dedupe()")
    deep = src.index("install_livedub_deep_audit()")

    # Quality guard establishes role filtering; provenance then replaces only the
    # heuristic track choice with the exact VOT artifact. The transactional local
    # sender must see that final lookup before dedupe/deep-audit capture it.
    assert companion < quality < provenance < new_atomic < cached_atomic < dedupe < deep
