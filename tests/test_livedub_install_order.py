from pathlib import Path


def test_transactional_audio_layers_are_final_before_capturing_wrappers():
    src = Path("bot_new.py").read_text(encoding="utf-8")

    companion = src.index("install_livedub_audio_companion()")
    cache_recovery = src.index("install_livedub_audio_cache_recovery()")
    quality = src.index("install_livedub_audio_quality_guard()")
    provenance = src.index("install_livedub_ru_provenance()")
    new_atomic = src.index("install_livedub_new_delivery_atomicity()")
    cached_atomic = src.index("install_livedub_cached_delivery_atomicity()")
    dedupe = src.index("install_livedub_audio_dedupe()")
    deep = src.index("install_livedub_deep_audit()")

    # Cache helpers must be recoverable before any later runtime uses them. Quality
    # establishes roles, provenance selects the exact VOT artifact, and transactional
    # senders remain final before dedupe/deep-audit capture their callables.
    assert (
        companion
        < cache_recovery
        < quality
        < provenance
        < new_atomic
        < cached_atomic
        < dedupe
        < deep
    )
