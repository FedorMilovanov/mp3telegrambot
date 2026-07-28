from __future__ import annotations

import json
from pathlib import Path

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_segment_normalizer as normalizer
from tools.voxcpm2 import generic_short_production as pipeline


ROOT = Path(__file__).resolve().parents[1]


def test_recipe_routes_only_to_clean_production() -> None:
    recipe = json.loads(
        (ROOT / "tools" / "voxcpm2" / "recipes" / "generic_short_v1.json").read_text(
            encoding="utf-8-sig"
        )
    )
    actions = recipe["actions"]
    assert actions["render_gemini"]["module"] == "tools.voxcpm2.generic_clean_gemini_runtime"
    assert actions["render_direct"]["module"] == "tools.voxcpm2.generic_clean_direct_runtime"
    assert actions["repair_audio"]["module"] == "tools.voxcpm2.generic_clean_audio_repair_runtime"
    production_modules = " ".join(
        actions[name]["module"]
        for name in ("render", "render_gemini", "render_direct", "repair_audio")
    )
    assert "_v45" not in production_modules
    assert "_v46" not in production_modules
    assert "_v47" not in production_modules


def test_clean_core_has_no_renderer_wrapper_installation() -> None:
    source = (ROOT / "tools" / "voxcpm2" / "clean_production_core.py").read_text(
        encoding="utf-8"
    )
    assert "import runpy" not in source
    assert "runpy.run_path" not in source
    assert "QualityV4SubprocessProxy" not in source
    assert "semantic_tts_guard_v4.install(" not in source
    assert "professional_audio_v45.install(" not in source
    assert "voxcpm2_cpu_shorts_production.py" in source
    assert "master_constant_mix.py" in source
    assert '"wrapper_count": 0' in source


def test_clean_entrypoints_disable_hidden_legacy_guard() -> None:
    for name in ("generic_clean_gemini_runtime.py", "generic_clean_direct_runtime.py"):
        source = (ROOT / "tools" / "voxcpm2" / name).read_text(encoding="utf-8")
        assert "install_runtime_adapters = _install_clean_runtime_adapters" in source
        assert "install_semantic_tts_guard" not in source
        assert "semantic_tts_guard_v4.install" not in source
        assert "semantic_tts_guard_v47" not in source
        assert "runpy.run_path" not in source


def test_clean_segmentation_caps_windows_at_54_seconds() -> None:
    cues = [
        pipeline.Cue(
            0.0,
            12.0,
            "This is a long spoken sentence that must be split into several short natural pieces without losing words.",
        )
    ]
    groups = clean.group_source_cues(cues)
    assert len(groups) >= 3
    assert all(float(item["end"]) - float(item["start"]) <= 5.4 + 0.001 for item in groups)
    original_words = " ".join(cue.text for cue in cues).split()
    grouped_words = " ".join(str(item["english"]) for item in groups).split()
    assert grouped_words == original_words


def test_tiny_one_word_segment_merges_without_word_loss() -> None:
    items = [
        {"id": 1, "start": 0.0, "source_end": 2.0, "text": "Первая фраза", "source": "one"},
        {"id": 2, "start": 2.0, "source_end": 2.7, "text": "Да", "source": "yes"},
        {"id": 3, "start": 2.7, "source_end": 5.0, "text": "Последняя фраза", "source": "last"},
    ]
    before = normalizer._tokens(items)
    merged = normalizer._merge_tiny(items)
    assert len(merged) == 2
    assert normalizer._tokens(merged) == before
    assert merged[0]["text"] == "Первая фраза Да"
    assert float(merged[0]["source_end"]) - float(merged[0]["start"]) <= 5.4


def test_global_delay_does_not_shorten_each_middle_window() -> None:
    groups = [
        {"id": 1, "start": 0.0, "end": 4.0, "source": "First source phrase."},
        {"id": 2, "start": 4.0, "end": 8.0, "source": "Second source phrase."},
        {"id": 3, "start": 8.0, "end": 12.0, "source": "Final source phrase."},
    ]
    translations = [
        {"id": 1, "russian": "Первая русская фраза."},
        {"id": 2, "russian": "Вторая русская фраза."},
        {"id": 3, "russian": "Последняя русская фраза."},
    ]
    segments, _ = clean.build_render_segments(
        groups,
        translations,
        delay_ms=420,
        duration=12.0,
    )
    assert segments[0]["end"] == 4.0
    assert segments[1]["end"] == 8.0
    assert segments[2]["end"] == 11.58
    assert all(item["start_delay_ms"] == 420 for item in segments)
    assert all(item["production_policy"] == clean.POLICY for item in segments)


def test_clean_master_is_quieter_and_release_safe() -> None:
    assert clean.MASTER_I == -16.0
    assert clean.MASTER_LRA == 8.0
    assert clean.MASTER_TP == -1.5


def test_clean_repair_requires_clean_baseline_for_selective_work() -> None:
    source = (
        ROOT / "tools" / "voxcpm2" / "generic_clean_audio_repair_runtime.py"
    ).read_text(encoding="utf-8")
    assert "Выборочный ремонт разрешён только после успешного чистого baseline" in source
    assert "force_fresh=repair_all" in source
    assert "clean_segment_normalizer.normalize" in source
    assert "semantic_tts_guard_v47" not in source
    assert "semantic_tts_guard_v46" not in source
    assert "professional_audio_v45.install" not in source
