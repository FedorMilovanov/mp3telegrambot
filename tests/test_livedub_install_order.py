from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES, RuntimePhase


def test_livedub_runtime_is_composed_from_explicit_contracts() -> None:
    features = {feature.feature_id: feature for feature in DEFAULT_RUNTIME_FEATURES}
    assert "livedub-qa-contract" in features
    assert "livedub-delivery-contract" in features
    assert features["livedub-qa-contract"].phase is RuntimePhase.POST_MAIN
    assert features["livedub-delivery-contract"].phase is RuntimePhase.POST_MAIN
    retired = {
        "livedub-audio-companion", "livedub-audio-cache-recovery", "livedub-audio-quality",
        "livedub-ru-provenance", "livedub-new-delivery-atomicity",
        "livedub-cached-delivery-atomicity", "livedub-audio-dedupe", "livedub-deep-audit",
    }
    assert retired.isdisjoint(features)
