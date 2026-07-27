from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.dub_projects import attach_approved_translation, create_project, load_project
from pipelines.dubbing import preflight


@pytest.fixture()
def configured_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
    monkeypatch.setenv("DUB_PROJECTS_DIR", str(tmp_path / "projects"))
    archive = tmp_path / "archive"
    snapshot = (
        archive
        / "models"
        / "voxcpm2-model-cache"
        / "models--openbmb--VoxCPM2"
        / "snapshots"
        / "test"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "model.safetensors").write_bytes(b"model")
    monkeypatch.setenv("VOXCPM2_ARCHIVE_ROOT", str(archive))
    monkeypatch.setenv("VOXCPM2_CPU_PYTHON", sys.executable)

    project_id = create_project(
        owner_user_id=1,
        source={"kind": "url", "url": "https://example.test/video"},
    )["project_id"]
    attach_approved_translation(
        project_id,
        text="Это окончательно утверждённый литературный перевод исходного выступления.",
        approved_by_user_id=1,
    )
    return project_id, tmp_path


def _patch_external_checks(monkeypatch: pytest.MonkeyPatch, duration: float) -> None:
    monkeypatch.setattr(
        preflight,
        "_probe_url",
        lambda _url: {
            "duration_seconds": duration,
            "probe_source": "test",
            "acodec": "opus",
            "vcodec": "avc1",
        },
    )
    monkeypatch.setattr(preflight, "_compile_python", lambda _python, _path: None)
    monkeypatch.setattr(
        preflight,
        "_probe_cpu_runtime",
        lambda _python: (
            {
                "python": "test",
                "torch": "test",
                "voxcpm": "installed",
                "cuda_available": False,
            },
            None,
        ),
    )
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/test/{name}")
    monkeypatch.setattr(
        preflight.importlib.util,
        "find_spec",
        lambda name: object() if name == "yt_dlp" else None,
    )


def test_short_preflight_enables_hardsub(
    configured_project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = configured_project
    _patch_external_checks(monkeypatch, 179.99)

    report = preflight.run_project_preflight(project_id)

    assert report["ok"] is True
    assert report["profile"] == "shorts_premium"
    assert report["subtitles"]["hardsub"] is True
    assert report["subtitles"]["translate_on_screen_text"] is False
    assert report["translation"]["rewrite_allowed"] is False
    assert report["translation"]["auto_shorten_allowed"] is False
    assert report["translation"]["units_sha256"]
    assert report["translation"]["contract_sha256"]
    assert report["synthesis"]["engine"] == "VoxCPM2"
    assert report["synthesis"]["device"] == "cpu"
    assert report["synthesis"]["runtime"]["cuda_available"] is False
    assert report["synthesis"]["hidden_tts_fallback"] is False
    manifest = load_project(project_id)
    assert manifest["status"] == "ready_for_production"
    assert manifest["production"]["ready"] is True
    assert (
        manifest["preflight"]["translation_contract_sha256"]
        == manifest["translation"]["contract_sha256"]
    )


def test_long_preflight_disables_hardsub(
    configured_project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = configured_project
    _patch_external_checks(monkeypatch, 1200.0)

    report = preflight.run_project_preflight(project_id)

    assert report["ok"] is True
    assert report["profile"] == "long_premium"
    assert report["subtitles"]["hardsub"] is False
    assert report["subtitles"]["separate_srt"] is True
    assert report["warnings"]


def test_container_tolerance_keeps_near_180_second_short(
    configured_project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = configured_project
    _patch_external_checks(monkeypatch, 180.034)
    report = preflight.run_project_preflight(project_id)
    assert report["profile"] == "shorts_premium"
    assert report["warnings"]


def test_changed_approved_translation_blocks_preflight(
    configured_project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = configured_project
    _patch_external_checks(monkeypatch, 60.0)
    manifest = load_project(project_id)
    translation_path = Path(manifest["translation"]["display_text_path"])
    translation_path.write_text("Текст был изменён после утверждения.", encoding="utf-8")

    report = preflight.run_project_preflight(project_id)

    assert report["ok"] is False
    assert any("изменился после блокировки" in item for item in report["blocking_errors"])
    assert load_project(project_id)["production"]["ready"] is False


def test_changed_translation_units_block_preflight(
    configured_project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = configured_project
    _patch_external_checks(monkeypatch, 60.0)
    manifest = load_project(project_id)
    units_path = Path(manifest["translation"]["units_path"])
    units = json.loads(units_path.read_text(encoding="utf-8"))
    units[0]["spoken_text"] += " Незаметная подмена."
    units_path.write_text(json.dumps(units, ensure_ascii=False), encoding="utf-8")

    report = preflight.run_project_preflight(project_id)

    assert report["ok"] is False
    assert any("единицы изменились" in item for item in report["blocking_errors"])


def test_cpu_runtime_that_sees_cuda_blocks_preflight(
    configured_project: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id, _ = configured_project
    _patch_external_checks(monkeypatch, 60.0)
    monkeypatch.setattr(
        preflight,
        "_probe_cpu_runtime",
        lambda _python: (
            {"cuda_available": True, "torch": "test"},
            "CPU-окружение неожиданно видит CUDA; production остановлен.",
        ),
    )

    report = preflight.run_project_preflight(project_id)

    assert report["ok"] is False
    assert any("видит CUDA" in item for item in report["blocking_errors"])


def test_local_source_without_audio_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "silent.mp4"
    source.write_bytes(b"not-a-real-video")
    payload = {
        "format": {"duration": "5.0"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
            }
        ],
    }
    monkeypatch.setattr(
        preflight,
        "_run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    with pytest.raises(Exception, match="нет аудиопотока"):
        preflight._probe_local_media(source)
