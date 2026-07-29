from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2 import clean_request_settings as settings
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair
from tools.voxcpm2 import generic_clean_direct_runtime as direct


ROOT = Path(__file__).resolve().parents[1]


def test_explicit_zero_settings_are_preserved() -> None:
    assert settings.values(
        {"original_level": 0, "russian_delay_ms": 0}
    ) == {
        "policy": settings.POLICY,
        "original_level": 0.0,
        "russian_delay_ms": 0,
    }
    assert settings.original_level({}) == pytest.approx(0.18)
    assert settings.russian_delay_ms({}) == 420


@pytest.mark.parametrize(
    "request",
    [
        {"original_level": True},
        {"original_level": float("nan")},
        {"original_level": 1.01},
        {"russian_delay_ms": True},
        {"russian_delay_ms": 1.5},
        {"russian_delay_ms": -1},
        {"russian_delay_ms": settings.MAX_RUSSIAN_DELAY_MS + 1},
    ],
)
def test_invalid_mix_settings_fail_closed(request) -> None:
    with pytest.raises(RuntimeError):
        settings.values(request)


def test_manifest_is_repaired_to_actual_zero_settings(tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = root / "output"
    output.mkdir(parents=True)
    manifest = {
        "phase": "completed",
        "original_level": 0.18,
        "russian_delay_ms": 420,
        "telegram_outputs": [
            {"label": "Готовый ролик: оригинал 18%, русский с задержкой 420 мс"},
            {"label": "Финальные русские субтитры с задержкой 420 мс"},
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    result = settings.repair_manifest(
        root,
        {"original_level": 0, "russian_delay_ms": 0},
    )
    assert result["settings_policy"] == settings.POLICY
    assert result["settings_delay_source"] == "request"
    assert result["original_level"] == 0.0
    assert result["russian_delay_ms"] == 0
    labels = [item["label"] for item in result["telegram_outputs"]]
    assert labels == [
        "Готовый ролик: оригинал 0%, русский без задержки",
        "Финальные русские субтитры без задержки",
    ]
    stored = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert stored == result


def test_audio_repair_manifest_uses_delay_proven_by_segments(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "project"
    output = root / "output"
    output.mkdir(parents=True)
    manifest_path = output / "manifest.json"
    manifest = {
        "phase": "completed",
        "original_level": 0.18,
        "russian_delay_ms": 420,
        "telegram_outputs": [
            {"label": "Готовый ролик: оригинал 18%, русский с задержкой 420 мс"},
            {"label": "Финальные русские субтитры с задержкой 420 мс"},
        ],
    }
    (root / "segments_ru_final.json").write_text(
        json.dumps(
            [
                {"id": 1, "start_delay_ms": 0},
                {"id": 2, "start_delay_ms": 0},
            ]
        ),
        encoding="utf-8",
    )

    def fake_legacy_update(path: Path, payload: dict, **_kwargs) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(repair, "_legacy_update_manifest", fake_legacy_update)
    monkeypatch.setattr(
        repair._legacy.production,
        "load_request",
        lambda _root: {"original_level": 0, "russian_delay_ms": 420},
    )
    repair._update_manifest(
        manifest_path,
        manifest,
        selected_ids=[1, 2],
        repair_all=True,
        seed=100,
        report_path=output / "audio_repair_report.json",
        marker={},
    )

    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stored["settings_delay_source"] == "segments"
    assert stored["original_level"] == 0.0
    assert stored["russian_delay_ms"] == 0
    assert [item["label"] for item in stored["telegram_outputs"]] == [
        "Готовый ролик: оригинал 0%, русский без задержки",
        "Финальные русские субтитры без задержки",
    ]


def test_repair_facade_preserves_runtime_helpers() -> None:
    assert callable(repair._next_seed)
    assert callable(repair._fingerprinted_baseline_ready)
    assert Path(repair.__file__).name == "__init__.py"


def test_direct_clean_wrapper_ignores_legacy_or_default(monkeypatch) -> None:
    captured: dict[str, int] = {}
    monkeypatch.setattr(
        direct,
        "_current_request",
        lambda: (Path("project"), {"russian_delay_ms": 0}),
    )

    def fake_builder(groups, *, delay_ms, duration):
        captured["delay_ms"] = delay_ms
        return [], []

    monkeypatch.setattr(direct.clean, "build_direct_segments", fake_builder)
    direct._build_clean_direct_segments(
        [{"id": 1, "source": "Текст"}],
        delay_ms=420,
        duration=10.0,
    )
    assert captured["delay_ms"] == 0


def test_all_clean_routes_repair_manifest_and_override_delay() -> None:
    expected = {
        "generic_clean_gemini_runtime.py": "_build_clean_render_segments",
        "generic_clean_custom_runtime.py": "_build_clean_render_segments",
        "generic_clean_direct_runtime.py": "_build_clean_direct_segments",
    }
    for filename, builder in expected.items():
        source = (ROOT / "tools" / "voxcpm2" / filename).read_text(encoding="utf-8")
        assert "from tools.voxcpm2 import clean_request_settings" in source
        assert "production._build_" in source
        assert builder in source
        assert "clean_request_settings.russian_delay_ms(request)" in source
        assert "clean_request_settings.repair_manifest(root, request)" in source
