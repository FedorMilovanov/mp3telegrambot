from __future__ import annotations

import asyncio
from pathlib import Path

import services.livedub_long_qa as long_qa
import services.livedub_qa as qa
import services.livedub_qa_hardening as hardening
import services.livedub_qa_trust as trust

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_manifest_has_one_source_owned_qa_contract():
    src = _src("services/runtime_manifest.py")
    for feature in (
        "livedub-long-qa",
        "livedub-qa-trust",
        "livedub-ru-provenance",
        "livedub-qa-hardening",
    ):
        assert f'"{feature}"' not in src
    assert '"livedub-qa-contract"' in src


def test_qa_strategy_modules_do_not_assign_into_other_modules():
    forbidden = (
        "module.run_translation_qa =",
        "module.format_qa_report =",
        "qa._issues_match =",
        "qa._confirmed_result =",
        "qa._env_int =",
        "qa._verify_candidate_windows =",
        "qa._insert_report_notes =",
        "yandex.get_live_dub_audio =",
        "mix.find_pro_tracks =",
    )
    for rel in (
        "services/livedub_long_qa.py",
        "services/livedub_qa_trust.py",
        "services/livedub_qa_hardening.py",
        "services/livedub_ru_provenance.py",
    ):
        src = _src(rel)
        assert "sys.meta_path" not in src
        assert all(token not in src for token in forbidden)


def test_public_qa_owner_forces_audio_truth_then_calls_long_and_trust(monkeypatch, tmp_path):
    seen = {}

    async def base(**kwargs):
        raise AssertionError("base runner should be delegated through long strategy")

    async def fake_long(base_runner, **kwargs):
        seen["long_base"] = base_runner
        seen["long"] = dict(kwargs)
        return {"issues": [{"time": "00:10", "severity": "minor"}]}

    async def fake_trust(base_runner, **kwargs):
        seen["trust_base"] = base_runner
        seen["trust"] = dict(kwargs)
        result = dict(kwargs["primary"])
        result["trusted"] = True
        return result

    monkeypatch.setattr(qa, "_run_translation_qa_base", base)
    monkeypatch.setattr(long_qa, "run_long_translation_qa", fake_long)
    monkeypatch.setattr(trust, "apply_audio_trust", fake_trust)
    monkeypatch.setattr(trust, "audio_trust_enabled", lambda: True)
    monkeypatch.setattr(hardening, "prepare_exact_timeline_inputs", lambda options: (dict(options), None))
    monkeypatch.setattr(hardening, "annotate_qa_availability", lambda result, options, exact: dict(result, audited=True))

    video = tmp_path / "dub.mp4"
    video.write_bytes(b"x")

    result = asyncio.run(qa.run_translation_qa(
        dub_video_path=video,
        original_audio_path=None,
        ai_data=None,
        duration=600,
        dub_srt_path=tmp_path / "untrusted.srt",
        dub_audio_path=None,
    ))
    assert result and result["trusted"] is True
    assert seen["long_base"] is base
    assert seen["trust_base"] is base
    assert seen["long"]["dub_srt_path"] is None
    assert seen["trust"]["primary"]["audited"] is True


def test_provenance_is_called_by_producer_and_consumer_directly():
    yandex = _src("services/yandex_live_dub.py")
    mix = _src("services/livedub_mix.py")
    provenance = _src("services/livedub_ru_provenance.py")
    assert "record_returned_ru_audio(" in yandex
    assert "snapshot_ru_audio_candidates(" in yandex
    assert "read_ru_audio_provenance(workdir)" in mix
    assert "yandex.get_live_dub_audio =" not in provenance
    assert "mix.find_pro_tracks =" not in provenance


def test_pre_main_keeps_long_quick_qa_reach_without_importing_qa_owner():
    src = _src("services/pre_main_policy.py")
    assert 'LIVEDUB_QUICK_QA_MAX_DURATION", "10800"' in src
    assert "import services.livedub_qa" not in src
