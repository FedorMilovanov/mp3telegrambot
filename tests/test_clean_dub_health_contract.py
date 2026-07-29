from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dub_health_checks_clean_entrypoints_only() -> None:
    source = (ROOT / "handlers" / "dub_health.py").read_text(encoding="utf-8")
    assert "tools.voxcpm2.generic_clean_gemini_runtime" in source
    assert "tools.voxcpm2.generic_clean_direct_runtime" in source
    assert "tools.voxcpm2.generic_clean_audio_repair_runtime" in source
    assert "generic_short_runtime.py" in source
    assert "clean_runtime_contract.py" in source
    assert "expressive_continuity.py" in source
    assert "continuous_reference_policy.py" in source
    assert "controlled_reference_gate.py" in source
    assert "russian_spoken_numbers.py" in source
    assert "expressive_translation.py" in source
    assert "direct_timbre_analysis.py" in source
    assert "final_media_qa.py" in source
    assert 'POLICY = "clean-runtime-contract-v2"' in source
    assert "sampled_sha256_file" in source
    assert '"sampled-begin-middle-end-v1"' in source
    assert 'root.rglob("*.py")' in source
    assert "'request.get(\"base_seed\") or' not in text[\"runtime_contract\"]" in source
    assert 'POLICY = "continuous-clean-reference-v2"' in source
    assert 'POLICY = "expressive-spoken-translation-v2"' in source
    assert 'translation-v2-bounded-gemini' in source
    assert 'DUB_GEMINI_REQUEST_TIMEOUT_SEC' in source
    assert 'DUB_GEMINI_PASS_TIMEOUT_SEC' in source
    assert 'types.HttpOptions(timeout=' in source
    assert 'time.monotonic() + pass_timeout' in source
    assert 'перевод 1/3' in source
    assert 'сверка 2/3' in source
    assert 'редактура 3/3' in source
    assert 'POLICY: Final = "russian-spoken-numbers-v2"' in source
    assert 'NUMERIC_SEMANTIC_POLICY = "wetext-aligned-exact-numeric-anchors-v2"' in source
    assert 'IDENTITY_POLICY = "calm-and-expressive-identity-v2"' in source
    assert '"single-continuous-window"' in source
    assert '"multi-window-fallback"' in source
    assert "_report_has_usable_selection" in source
    assert "continuous_reference_policy.build_calm_references" in source
    assert "MIN_IDENTITY_SPECTRAL_SIMILARITY = 0.55" in source
    assert "identity_reference=extended" in source
    assert "numeric_anchors_passed" in source
    assert "MAX_START_DELAY_MS = 1500" in source
    assert "_finite_voice_metric" in source
    assert "MAX_TIMBRE_PENALTY" in source
    assert "pre-model-reference-hard-floor-v1" in source
    assert "afade=t=in" in source
    assert "afade=t=out" in source
    assert "fixed-original-post-russian-master-v1" in source
    assert '"post_mix_loudnorm": False' in source
    assert '"post_mix_limiter": False' in source
    assert "verify_final_outputs" in source
    assert "final_media_verification.json" in source
    assert "Clean Expressive NoChew + независимый QA" in source
    assert "NoChew Quality v4.2 + аудиоремонт" not in source
    assert "generic_direct_checked_runtime" not in source
    assert 'semantic_tts_guard_v4.install()" in contract_text' not in source


def test_dub_health_keeps_fourteen_logical_checks() -> None:
    source = (ROOT / "handlers" / "dub_health.py").read_text(encoding="utf-8")
    labels = (
        "Recipe: Gemini MAX",
        "Recipe: готовый SRT",
        "Recipe: чистый аудиоремонт",
        "Clean Expressive NoChew + независимый QA",
        "Whisper semantic QA",
        "SoundFile WAV I/O",
        "VoxCPM2 CPU Python",
        "VoxCPM2 archive",
        "Gemini MAX keys",
        "Dub Studio storage",
        "Worker",
        "Python UTF-8",
    )
    for label in labels:
        assert label in source
    assert 'for binary in ("ffmpeg", "ffprobe")' in source
