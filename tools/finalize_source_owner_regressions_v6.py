#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


def replace_top_level_function(path: Path, name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            lines[start:node.end_lineno] = [replacement.rstrip() + "\n"]
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise RuntimeError(f"missing top-level function {name} in {path}")


contract = Path("tools/voxcpm2/clean_runtime_contract.py")
text = contract.read_text(encoding="utf-8")
self_path = "'tools/voxcpm2/clean_runtime_contract.py'"
if self_path not in text:
    anchor = "_RENDER_MODULES = ("
    if anchor not in text:
        raise SystemExit("clean_runtime_contract _RENDER_MODULES anchor missing")
    text = text.replace(anchor, anchor + self_path + ", ", 1)
contract.write_text(text, encoding="utf-8")

health = Path("handlers/dub_health.py")
replace_top_level_function(
    health,
    "_quality_contract",
    r'''def _quality_contract(repo: Path) -> tuple[bool, str]:
    root = Path(repo)
    voxcpm = root / "tools" / "voxcpm2"
    required = {
        "runtime_contract": voxcpm / "clean_runtime_contract.py",
        "core": voxcpm / "clean_production_core.py",
        "source_download": voxcpm / "clean_source_download.py",
        "request_settings": voxcpm / "clean_request_settings.py",
        "translation": voxcpm / "strict_translation_payload.py",
        "gemini": voxcpm / "generic_gemini_runtime.py",
        "direct": voxcpm / "generic_direct_runtime.py",
        "custom": voxcpm / "generic_custom_runtime.py",
        "repair": voxcpm / "generic_clean_audio_repair_runtime.py",
        "semantic_blocks": voxcpm / "semantic_block_runtime.py",
        "direct_io": voxcpm / "direct_max_quality_io.py",
        "retry_epoch": voxcpm / "direct_retry_epoch.py",
        "direct_master": voxcpm / "master_direct_russian_only.py",
        "preflight": voxcpm / "dub_job_preflight.py",
        "backend": root / "services" / "speech_backends" / "voxcpm2.py",
        "worker": root / "services" / "dub_worker.py",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        return False, "не найдены canonical owners: " + ", ".join(sorted(missing))
    text = {name: _read(path) for name, path in required.items()}
    failed: list[str] = []

    forbidden = ("sys.modules[", "setattr(module", "def install_runtime", "def install_preflight", "ContextVar(")
    for name in ("gemini", "direct", "custom", "repair", "direct_master", "preflight"):
        if any(token in text[name] for token in forbidden):
            failed.append("runtime-safety")
            break

    expected_routes = {
        "render": "tools.voxcpm2.generic_gemini_runtime",
        "render_gemini": "tools.voxcpm2.generic_gemini_runtime",
        "render_direct": "tools.voxcpm2.generic_direct_runtime",
        "repair_audio": "tools.voxcpm2.generic_clean_audio_repair_runtime",
        "prepare_custom": "tools.voxcpm2.generic_custom_runtime",
        "render_custom": "tools.voxcpm2.generic_custom_runtime",
    }
    try:
        recipe = load_recipe("generic_short_v1")
        recipe_ok = all(
            str(recipe.action(action).get("runner") or "") == "python_module"
            and str(recipe.action(action).get("module") or "") == module
            for action, module in expected_routes.items()
        )
    except Exception:
        recipe_ok = False
    if not recipe_ok:
        failed.append("recipe-routing")

    if not (
        'POLICY = spatial_bed_contract.POLICY' in text["direct_master"]
        and '"source_bed_applied": False' in text["direct_master"]
        and '"applied_original_level": 0.0' in text["direct_master"]
        and "master_monolithic_mix" not in text["direct_master"]
        and "tools.voxcpm2.master_direct_russian_only" in text["backend"]
        and "master_direct_russian_only.py" in text["core"]
    ):
        failed.append("direct-master")

    if not (
        'POLICY = "clean-runtime-contract-v2"' in text["runtime_contract"]
        and "tools/voxcpm2/clean_runtime_contract.py" in text["runtime_contract"]
        and "tools/voxcpm2/master_direct_russian_only.py" in text["runtime_contract"]
        and "tools/voxcpm2/generic_project_runtime.py" in text["runtime_contract"]
        and "tools/voxcpm2/generic_direct_runtime.py" in text["runtime_contract"]
        and "tools/voxcpm2/generic_clean_audio_repair_runtime.py" in text["runtime_contract"]
        and "def build_fingerprints(" in text["runtime_contract"]
    ):
        failed.append("fingerprints")

    if not (
        'PREFLIGHT_JSON_TRANSPORT_POLICY = "marked-preflight-json-transport-v2"' in text["preflight"]
        and "backend.runtime_paths(repo, request)" in text["preflight"]
        and "backend.process_environment(" in text["preflight"]
        and "def _decode_probe_payload(" in text["preflight"]
    ):
        failed.append("preflight")

    if not (
        "def build_command(" in text["worker"]
        and "from tools.voxcpm2 import dub_job_preflight" in text["worker"]
        and "from services.dub_worker import build_command" in _read(health)
    ):
        failed.append("worker")

    if not (
        'POLICY = "voxcpm2-direct-max-quality-v3"' in text["direct_io"]
        and "from collections.abc import Mapping" in text["retry_epoch"]
        and "semantic_block_runtime.build_direct_segments(" in text["direct"]
        and "ProjectRoute" in text["gemini"]
        and "ProjectRoute" in text["custom"]
    ):
        failed.append("direct-runtime")

    if failed:
        return False, "не прошли: " + ", ".join(failed)
    return True, (
        "runtime-safety; recipe-routing; direct-master Russian-only; fingerprints; "
        "source-owned preflight; services.dub_worker; typed direct retry"
    )''',
)

Path("tests/test_generic_direct_runtime.py").write_text(
r'''from __future__ import annotations

from tools.voxcpm2.generic_direct_runtime import (
    _build_direct_segments,
    group_srt_cues,
    normalize_srt_cues,
    parse_srt_text,
)
from tools.voxcpm2.generic_short_production import Cue


def test_parse_srt_preserves_user_words_and_punctuation() -> None:
    text = """1
00:00:00,000 --> 00:00:02,000
<i>[Важно:] Это мой окончательный перевод.</i>

2
00:00:02,000 --> 00:00:04,000
Ничего не переписывать!
"""
    cues = parse_srt_text(text)
    assert [cue.text for cue in cues] == [
        "[Важно:] Это мой окончательный перевод.",
        "Ничего не переписывать!",
    ]


def test_normalize_overlapping_srt_keeps_all_text() -> None:
    cues = [Cue(0.0, 2.0, "Первая фраза."), Cue(1.8, 3.0, "Вторая фраза.")]
    normalized, adjustments = normalize_srt_cues(cues, 4.0)
    combined = " ".join(cue.text for cue in normalized)
    assert "Первая фраза." in combined
    assert "Вторая фраза." in combined
    assert adjustments


def test_grouping_preserves_every_word_in_order() -> None:
    cues = [
        Cue(0.0, 2.0, "Один два."),
        Cue(2.0, 4.0, "Три четыре."),
        Cue(4.0, 6.0, "Пять шесть."),
    ]
    groups = group_srt_cues(cues)
    assert " ".join(group["source"] for group in groups) == "Один два. Три четыре. Пять шесть."


def test_direct_segments_apply_420ms_delay_without_rewriting() -> None:
    blocks = [{
        "id": 1,
        "start": 1.0,
        "end": 4.0,
        "source": "Точный текст.",
        "semantic_block_id": 1,
        "source_cue_count": 1,
        "semantic_block_duration": 3.0,
        "source_parts": ["Точный текст."],
    }]
    segments, subtitles = _build_direct_segments(blocks, delay_ms=420, duration=5.0)
    assert segments[0]["start_delay_ms"] == 420
    assert segments[0]["text"] == "Точный текст."
    assert subtitles[0].start == 1.42
    assert subtitles[0].text == "Точный текст."
''', encoding="utf-8")

Path("tests/test_audio_conversion_postcondition.py").write_text(
r'''from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from services import mp3_conversion


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_publishes_only_after_probe(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")
    async def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"encoded" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    async def fake_probe(path):
        return Path(path).is_file() and Path(path).stat().st_size > 1024
    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is True
    assert output.is_file()
    assert not list(tmp_path.glob("*.part-*.mp3"))


@pytest.mark.asyncio
async def test_atomic_mp3_conversion_keeps_existing_output_on_failed_probe(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp3"
    output = tmp_path / "out.mp3"
    source.write_bytes(b"source" * 4096)
    output.write_bytes(b"old" * 4096)
    old = output.read_bytes()
    monkeypatch.setattr(mp3_conversion.shutil, "which", lambda name: f"/{name}")
    async def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"bad" * 4096)
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    async def fake_probe(_path):
        return False
    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    source.touch()
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is False
    assert output.read_bytes() == old
''', encoding="utf-8")

print("source-owner regression finalizer v6 applied")
