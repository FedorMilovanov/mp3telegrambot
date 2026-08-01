from services.runtime_manifest import DEFAULT_RUNTIME_FEATURES


def _feature_order() -> dict[str, int]:
    return {
        feature.feature_id: index
        for index, feature in enumerate(DEFAULT_RUNTIME_FEATURES)
    }


def test_transactional_audio_layers_are_final_before_capturing_wrappers():
    order = _feature_order()

    assert (
        order["livedub-audio-companion"]
        < order["livedub-audio-cache-recovery"]
        < order["livedub-audio-quality"]
        < order["livedub-ru-provenance"]
        < order["livedub-new-delivery-atomicity"]
        < order["livedub-cached-delivery-atomicity"]
        < order["livedub-audio-dedupe"]
        < order["livedub-deep-audit"]
    )
