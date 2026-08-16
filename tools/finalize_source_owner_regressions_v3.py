#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path


def write(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text.rstrip() + "\n", encoding="utf-8")


def remove_functions(path: str, names: set[str]) -> None:
    target = Path(path)
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(target))
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        )
    ]
    ast.fix_missing_locations(tree)
    target.write_text(ast.unparse(tree).rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Production P0s: source-own the direct master and restore the typed retry import.
# ---------------------------------------------------------------------------
retry = Path("tools/voxcpm2/direct_retry_epoch.py")
text = retry.read_text(encoding="utf-8")
if "from collections.abc import Mapping" not in text:
    anchor = "from __future__ import annotations\n"
    if anchor not in text:
        raise SystemExit("direct_retry_epoch future-import anchor missing")
    text = text.replace(anchor, anchor + "from collections.abc import Mapping\n", 1)
retry.write_text(text, encoding="utf-8")

backend = Path("services/speech_backends/voxcpm2.py")
text = backend.read_text(encoding="utf-8")
text = text.replace(
    '_DIRECT_MASTER_MODULE = "tools.voxcpm2.master_monolithic_mix"',
    '_DIRECT_MASTER_MODULE = "tools.voxcpm2.master_direct_russian_only"',
)
text = text.replace(
    'repo / "tools" / "voxcpm2" / "master_monolithic_mix.py",',
    'repo / "tools" / "voxcpm2" / "master_direct_russian_only.py",',
)
if "master_monolithic_mix" in text:
    raise SystemExit("VoxCPM backend still references retired master_monolithic_mix")
backend.write_text(text, encoding="utf-8")

core = Path("tools/voxcpm2/clean_production_core.py")
text = core.read_text(encoding="utf-8")
text = text.replace(
    'MASTER_ENTRYPOINT_NAMES = frozenset({"master_constant_mix.py", "master_monolithic_mix.py"})',
    'MASTER_ENTRYPOINT_NAMES = frozenset({"master_constant_mix.py", "master_direct_russian_only.py"})',
)
if '"master_monolithic_mix.py"' in text:
    raise SystemExit("clean_production_core still recognizes retired master")
core.write_text(text, encoding="utf-8")

contract = Path("tools/voxcpm2/clean_runtime_contract.py")
text = contract.read_text(encoding="utf-8")
direct_master = "'tools/voxcpm2/master_direct_russian_only.py'"
if direct_master not in text:
    anchor = "'tools/voxcpm2/examples/john_piper_z20py4yqhyq/master_constant_mix.py'"
    if anchor not in text:
        raise SystemExit("clean_runtime_contract release master anchor missing")
    text = text.replace(anchor, anchor + ", " + direct_master, 1)
contract.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Focused test fixes made robust against ast.unparse from earlier finalizers.
# ---------------------------------------------------------------------------
gemini_test = Path("tests/test_gemini_translation_quality.py")
source = gemini_test.read_text(encoding="utf-8")
tree = ast.parse(source, filename=str(gemini_test))
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or len(node.args) != 3:
        continue
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "dub_wizard"
        and func.attr == "_request_payload"
    ):
        node.args.append(
            ast.Attribute(
                value=ast.Name(id="dub_wizard", ctx=ast.Load()),
                attr="DEFAULT_MODEL_PROFILE_ID",
                ctx=ast.Load(),
            )
        )
ast.fix_missing_locations(tree)
gemini_test.write_text(ast.unparse(tree).rstrip() + "\n", encoding="utf-8")
remove_functions(
    str(gemini_test),
    {"test_translation_keeps_high_thinking_and_bounded_network_calls"},
)
with gemini_test.open("a", encoding="utf-8") as handle:
    handle.write(
        '''\n\ndef test_translation_keeps_high_thinking_and_bounded_network_calls() -> None:\n'''
        '''    runtime = _source(RUNTIME)\n'''
        '''    assert 'thinking_level="high"' in runtime\n'''
        '''    assert "types.ThinkingConfig" in runtime\n'''
        '''    assert 'response_mime_type="application/json"' in runtime\n'''
        '''    assert "max_output_tokens=16000" in runtime\n'''
        '''    assert "types.HttpOptions(timeout=" in runtime\n'''
        '''    assert "DUB_GEMINI_REQUEST_TIMEOUT_SEC" in runtime\n'''
        '''    assert "DUB_GEMINI_PASS_TIMEOUT_SEC" in runtime\n'''
        '''    assert "time.monotonic() + pass_timeout" in runtime\n'''
        '''    assert "remaining < _MIN_REQUEST_TIMEOUT_SECONDS" in runtime\n'''
        '''    assert "load_dotenv(override=False)" in runtime\n'''
    )

repair_test = Path("tests/test_clean_request_settings.py")
remove_functions(str(repair_test), {"test_repair_facade_preserves_runtime_helpers"})
with repair_test.open("a", encoding="utf-8") as handle:
    handle.write(
        '''\n\ndef test_repair_owner_preserves_runtime_helpers() -> None:\n'''
        '''    assert callable(repair._next_seed)\n'''
        '''    assert callable(repair._fingerprinted_baseline_ready)\n'''
        '''    assert callable(repair._validate_repair_request)\n'''
        '''    assert Path(repair.__file__).name == "generic_clean_audio_repair_runtime.py"\n'''
    )


# ---------------------------------------------------------------------------
# Canonical source-owner replacements for tests that imported retired wrappers.
# ---------------------------------------------------------------------------
write(
    "tests/test_preflight_json_protocol.py",
    r'''from __future__ import annotations

import json

import pytest

from tools.voxcpm2 import dub_job_preflight as preflight


def _payload() -> dict[str, object]:
    return {
        "python": r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe",
        "loaded": {"voxcpm": r"C:\runtime\voxcpm\__init__.py"},
    }


def _encode(payload: object) -> str:
    return preflight.PREFLIGHT_JSON_MARKER + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def test_marked_payload_survives_import_noise_before_and_after() -> None:
    payload = _payload()
    stdout = "\n".join(("Loading VoxCPM runtime...", _encode(payload), "shutdown diagnostic"))
    decoded, noise = preflight._decode_probe_payload(stdout)
    assert decoded == payload
    assert "Loading VoxCPM runtime" in noise
    assert "shutdown diagnostic" in noise
    assert preflight.PREFLIGHT_JSON_MARKER not in noise


def test_last_valid_marked_payload_wins() -> None:
    current = _payload()
    decoded, noise = preflight._decode_probe_payload(
        "\n".join((_encode({"python": "old", "loaded": {}}), "banner", _encode(current)))
    )
    assert decoded == current
    assert "banner" in noise


def test_plain_or_corrupt_json_is_rejected_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="маркированный JSON"):
        preflight._decode_probe_payload(json.dumps(_payload(), ensure_ascii=False))
    with pytest.raises(RuntimeError, match="маркированный JSON") as exc:
        preflight._decode_probe_payload("banner\n" + preflight.PREFLIGHT_JSON_MARKER + "{broken")
    assert "banner" in str(exc.value)


def test_payload_must_be_an_object() -> None:
    with pytest.raises(RuntimeError, match="маркированный JSON"):
        preflight._decode_probe_payload(_encode(["not", "an", "object"]))


def test_preflight_protocol_is_source_owned() -> None:
    assert preflight.PREFLIGHT_JSON_TRANSPORT_POLICY == "marked-preflight-json-transport-v2"
    assert callable(preflight._runtime_paths)
    assert callable(preflight._probe_imports)
    source = __import__("pathlib").Path(preflight.__file__).read_text(encoding="utf-8")
    assert "def install_preflight_json" not in source
    assert "sys.modules" not in source
''',
)

write(
    "tests/test_speech_backend_runtime_paths.py",
    r'''from __future__ import annotations

from pathlib import Path

from services.speech_backends import get_backend
from tools.voxcpm2 import dub_job_preflight


def test_direct_backend_selects_source_owned_russian_only_master() -> None:
    backend = get_backend("voxcpm2")
    repo = Path(__file__).resolve().parents[1]
    runtime = backend.runtime_paths(
        repo,
        {
            "translation_mode": "direct",
            "cpu_venv": r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
            "vox_archive": r"C:\AI-Archive\VoxCPM2-paused-RTX3060",
        },
    )
    assert runtime.master_entrypoint.name == "master_direct_russian_only.py"
    assert runtime.master_module == "tools.voxcpm2.master_direct_russian_only"
    assert runtime.final_qa_module == "tools.voxcpm2.final_media_qa"


def test_preflight_uses_backend_owned_runtime_paths_without_installers() -> None:
    source = Path(dub_job_preflight.__file__).read_text(encoding="utf-8")
    assert "backend.runtime_paths(repo, request)" in source
    assert "backend.process_environment(" in source
    assert "def _runtime_paths(" in source
    assert "def _probe_imports(" in source
    assert "preflight_json_protocol" not in source
    assert "def install" not in source
''',
)

write(
    "tests/test_direct_master_source_owner.py",
    r'''from __future__ import annotations

from pathlib import Path

from services.speech_backends import get_backend
from tools.voxcpm2 import master_direct_russian_only as master


def test_russian_only_mix_never_uses_source_audio(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(master.base, "run", lambda command: commands.append(list(command)))
    source = tmp_path / "source.mp4"
    russian = tmp_path / "russian.wav"
    output = tmp_path / "mix.wav"
    graph = master.build_russian_only_mix(
        source=source,
        mastered_russian=russian,
        output=output,
        source_duration=12.5,
        original_level=0.18,
        russian_gain=1.0,
    )
    assert commands
    command = commands[0]
    assert str(russian) in command
    assert str(source) not in command
    assert "-af" in command
    assert "volume=1.000000000" in graph
    assert master.POLICY.startswith("russian-only-direct-master")


def test_backend_points_direct_mode_to_real_owner() -> None:
    backend = get_backend("voxcpm2")
    repo = Path(__file__).resolve().parents[1]
    path, module = backend._master_contract(repo, {"translation_mode": "direct"}) if hasattr(backend, "_master_contract") else (None, None)
    if path is None:
        from services.speech_backends import voxcpm2
        path, module = voxcpm2._master_contract(repo, {"translation_mode": "direct"})
    assert path.name == "master_direct_russian_only.py"
    assert module == "tools.voxcpm2.master_direct_russian_only"


def test_direct_master_has_no_runtime_surgery() -> None:
    source = Path(master.__file__).read_text(encoding="utf-8")
    assert "def install(" not in source
    assert "sys.modules" not in source
    assert "setattr(" not in source
    assert "master_monolithic_mix" not in source
''',
)

# generic direct: preserve verbatim/timing behavior, remove checked-wrapper-only assertions.
p = Path("tests/test_generic_direct_runtime.py")
text = p.read_text(encoding="utf-8")
text = text.replace(
    "from tools.voxcpm2.generic_direct_checked_runtime import (\n    build_direct_segments_safe,\n    preserve_user_tts_text,\n)\n",
    "",
)
p.write_text(text, encoding="utf-8")
remove_functions(
    str(p),
    {
        "test_checked_entrypoint_expands_sub_350ms_final_cue",
        "test_ready_srt_tts_policy_keeps_final_punctuation_verbatim",
    },
)

# Generic short recipe tests must use the real worker owner.
p = Path("tests/test_generic_short_production.py")
text = p.read_text(encoding="utf-8")
text = text.replace(
    "from tools.voxcpm2.dub_worker import build_command",
    "from services.dub_worker import build_command",
)
p.write_text(text, encoding="utf-8")

# Strict translation payload: retarget actual-language behavior to the source-owned Gemini route.
p = Path("tests/test_strict_translation_payload.py")
text = p.read_text(encoding="utf-8")
text = text.replace(
    "from tools.voxcpm2 import generic_clean_gemini_runtime",
    "from tools.voxcpm2 import generic_gemini_runtime",
)
text = text.replace(
    'custom = (ROOT / "tools" / "voxcpm2" / "generic_clean_custom_runtime.py").read_text(encoding="utf-8")',
    'custom = (ROOT / "tools" / "voxcpm2" / "generic_custom_runtime.py").read_text(encoding="utf-8")',
)
text = text.replace(
    'assert "production._validate_translation_payload = strict_translation_payload.validate_full" in custom',
    'assert "validate_translation=strict_translation_payload.validate_full" in custom',
)
p.write_text(text, encoding="utf-8")
remove_functions(str(p), {"test_actual_transcript_language_overrides_stale_metadata"})
with p.open("a", encoding="utf-8") as handle:
    handle.write(
        '''\n\ndef test_actual_transcript_language_overrides_stale_metadata(monkeypatch) -> None:\n'''
        '''    metadata = {"title": "Video", "language": "en"}\n'''
        '''    cues = [object()]\n'''
        '''    monkeypatch.setattr(generic_gemini_runtime.production, "acquire_transcript", lambda *_a, **_k: (cues, "whisper", "de"))\n'''
        '''    result = generic_gemini_runtime._acquire_transcript_clean(\n'''
        '''        "https://youtu.be/AbCdEf12345", Path("source.mp4"), Path("source"), metadata,\n'''
        '''        whisper_model="large-v3", duration=10.0,\n'''
        '''    )\n'''
        '''    assert result == (cues, "whisper", "de")\n'''
        '''    assert metadata["language"] == "de"\n'''
        '''    assert metadata["source_language"] == "de"\n'''
        '''    source = (ROOT / "tools" / "voxcpm2" / "generic_gemini_runtime.py").read_text(encoding="utf-8")\n'''
        '''    assert "acquire_transcript=_acquire_transcript_clean" in source\n'''
        '''    assert "production.acquire_transcript =" not in source\n'''
    )

# Direct surgical utility tests: remove installers; keep pure utility contracts.
p = Path("tests/test_direct_surgical_io.py")
text = p.read_text(encoding="utf-8")
text = text.replace("from tools.voxcpm2 import direct_surgical_polish_v2\n", "")
text = text.replace("\ndirect_surgical_polish_v2.install_global_polish()\n", "\n")
p.write_text(text, encoding="utf-8")

p = Path("tests/test_direct_surgical_guard.py")
text = p.read_text(encoding="utf-8")
text = text.replace("from tools.voxcpm2 import direct_surgical_guard as surgical_guard\n", "")
text = text.replace("\nsurgical_guard.install_guard_contract()\n", "\n")
text = text.replace("\ndirect_surgical_polish_v2.install_global_polish()\n", "\n")
p.write_text(text, encoding="utf-8")

write(
    "tests/test_direct_surgical_recovery.py",
    r'''from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from tools.voxcpm2 import direct_failure_recovery as recovery
from tools.voxcpm2 import direct_timing_guard as guard


def test_structured_failure_advances_exactly_once(tmp_path: Path, monkeypatch) -> None:
    calls = []
    item = {"id": 3, "text": "Текст", "start": 0.0, "end": 4.0, "tail_guard": 0.18}
    def original():
        raise guard.RetryableSynthesisFailure(
            "Сегмент #3: measured failure", segment=item,
            evidence={"kind": "measured"}, advance_retry=True,
            failure_kind="measured_timing_failure",
        )
    def invalidate(work_dir, segment, **kwargs):
        calls.append((Path(work_dir), segment, kwargs))
        return {"last_scope_epoch": 2}
    monkeypatch.setattr(sys, "argv", ["renderer", f"--work-dir={tmp_path / 'work'}"])
    with pytest.raises(RuntimeError, match="Retry scope advanced to 2"):
        recovery.run_with_failure_recovery(original, invalidate)
    assert len(calls) == 1
    assert calls[0][2]["evidence"]["early_stop_kind"] == "measured_timing_failure"


def test_blocked_identical_repeat_never_advances(tmp_path: Path, monkeypatch) -> None:
    calls = []
    item = {"id": 3, "text": "Текст", "start": 0.0, "end": 4.0, "tail_guard": 0.18}
    def original():
        raise guard.RetryableSynthesisFailure(
            "Сегмент #3: unchanged marker", segment=item,
            evidence={"kind": "repeat"}, advance_retry=False,
            failure_kind="unchanged_timing_block",
        )
    monkeypatch.setattr(sys, "argv", ["renderer", "--work-dir", str(tmp_path / "work")])
    with pytest.raises(guard.RetryableSynthesisFailure, match="unchanged marker"):
        recovery.run_with_failure_recovery(original, lambda *a, **k: calls.append((a, k)))
    assert calls == []


def test_legacy_message_fallback_supports_equals_flags(tmp_path: Path, monkeypatch) -> None:
    segments = tmp_path / "segments.json"
    segments.write_text(json.dumps([{"id": 3, "text": "Текст"}]), encoding="utf-8")
    calls = []
    def original():
        raise RuntimeError("Сегмент #3: адаптивный бюджет 3 кандидатов исчерпан")
    def invalidate(work_dir, segment, **kwargs):
        calls.append((Path(work_dir), segment, kwargs))
        return {"retry_epoch": 1}
    monkeypatch.setattr(sys, "argv", ["renderer", f"--work-dir={tmp_path / 'work'}", f"--segments-json={segments}"])
    with pytest.raises(RuntimeError, match="Retry scope advanced to 1"):
        recovery.run_with_failure_recovery(original, invalidate)
    assert len(calls) == 1


def test_unrelated_runtime_error_is_not_intercepted() -> None:
    def original():
        raise RuntimeError("ffmpeg missing")
    with pytest.raises(RuntimeError, match="ffmpeg missing"):
        recovery.run_with_failure_recovery(original, lambda *a, **k: None)
''',
)

# Audio repair: preserve real owner/recipe behavior; drop bootstrap compatibility tests.
p = Path("tests/test_dub_audio_repair.py")
text = p.read_text(encoding="utf-8")
text = text.replace("from tools.voxcpm2 import dub_worker\n", "from services import dub_worker\n")
text = text.replace(
    "from tools.voxcpm2.generic_audio_repair_runtime_bootstrap import (\n    build_recovered_manifest,\n    ensure_repair_manifest,\n)\n",
    "",
)
p.write_text(text, encoding="utf-8")
remove_functions(
    str(p),
    {
        "test_missing_manifest_is_recovered_without_translation_call",
        "test_manifest_recovery_rejects_project_without_finished_segments",
    },
)
text = p.read_text(encoding="utf-8")
text = text.replace(
    '"tools/voxcpm2/generic_clean_audio_repair_runtime/__init__.py"',
    '"tools/voxcpm2/generic_clean_audio_repair_runtime.py"',
)
p.write_text(text, encoding="utf-8")

# Factory tests: retain language/preflight/source-owner behavior, retire the ContextVar cut shim tests.
write(
    "tests/test_factory_execution_and_cut_source_policy.py",
    r'''from pathlib import Path

import pytest

from services.shorts_factory_execution_guard import (
    factory_language_needs_translation,
    factory_preflight_issues,
    factory_translation_preflight_issues,
    normalize_factory_language,
    resolve_factory_spoken_language,
)
from services.shorts_factory_quality_gate import validated_factory_plan_language


def test_factory_quality_gate_requires_proven_dominant_spoken_language():
    assert validated_factory_plan_language({"metadata": {"language": "en"}}) == "en"
    with pytest.raises(RuntimeError, match="доминирующий язык речи"):
        validated_factory_plan_language({"metadata": {"language": "mixed"}})


def test_factory_spoken_language_prefers_audio_plan_over_title_metadata():
    assert resolve_factory_spoken_language({"metadata": {"language": "English"}}, {"title": "Русский"}) == "en"
    assert factory_language_needs_translation("en") is True


def test_factory_russian_audio_skips_translation_even_with_english_title():
    assert resolve_factory_spoken_language({"metadata": {"language": "русский"}}, {"title": "English title"}) == "ru"
    assert factory_language_needs_translation("ru") is False


@pytest.mark.parametrize(("value", "expected"), [("en-US", "en"), ("English", "en"), ("русский", "ru"), ("ukr", "uk"), ("Belarusian", "be"), ("fr-FR", "fr")])
def test_factory_language_normalization(value, expected):
    assert normalize_factory_language(value) == expected


@pytest.mark.parametrize("language", ["uk", "be", "en", "fr", "de"])
def test_every_proven_non_russian_language_requires_russian_livedub(language):
    assert factory_language_needs_translation(language) is True


def test_factory_unknown_language_is_fail_closed_without_title_guessing():
    with pytest.raises(RuntimeError, match="Не удалось доказать язык речи"):
        resolve_factory_spoken_language({"metadata": {"language": "unknown"}}, {"title": "Евангелие"})


def test_factory_translation_preflight_requires_route_and_oauth_by_default():
    assert factory_translation_preflight_issues(oauth_present=False, helper_available=False, cli_available=False, require_oauth=True) == (
        "Yandex LiveDub client route is unavailable",
        "VOT_API_TOKEN/YANDEX_OAUTH_TOKEN is missing",
    )


def test_factory_translation_preflight_allows_explicit_cached_only_opt_out():
    assert factory_translation_preflight_issues(oauth_present=False, helper_available=True, cli_available=False, require_oauth=False) == ()


def test_factory_preflight_reports_every_missing_runtime_dependency():
    assert factory_preflight_issues(gemini_available=False, whisper_available=False, ffmpeg_available=False, ffprobe_available=False, free_gb=0.4, min_free_gb=2.0) == (
        "Gemini API clients are unavailable",
        "faster-whisper is unavailable",
        "ffmpeg is unavailable",
        "ffprobe is unavailable",
        "free disk 0.4 GB is below 2.0 GB",
    )


def test_factory_owner_proves_language_before_selecting_source_backend():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")
    plan_pos = source.index("plan = await create_factory_plan(")
    language_pos = source.index("spoken_language = resolve_factory_spoken_language(plan, info)")
    source_task_pos = source.index("source_task = asyncio.create_task(", language_pos)
    assert plan_pos < language_pos < source_task_pos
    assert "_source_needs_translation" not in source
    assert "factory_language_needs_translation(spoken_language)" in source


def test_factory_quality_and_execution_contracts_are_not_installer_stacks():
    quality = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")
    execution = Path("services/shorts_factory_execution_guard.py").read_text(encoding="utf-8")
    assert "install_factory_plan_quality_gate" not in quality
    assert "install_shorts_factory_execution_guard" not in execution
    assert "shorts_factory_runtime" not in execution
''',
)

# LiveDub delivery: replace retired interception-module tests with the explicit coordinator boundary.
write(
    "tests/test_livedub_delivery_coordinator_source_owned.py",
    r'''from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services import livedub_delivery_coordinator as delivery


@pytest.mark.asyncio
async def test_singleflight_runs_one_companion_transaction() -> None:
    delivery._COMPANION_INFLIGHT.clear()
    delivery._COMPANION_SENT.clear()
    calls = 0
    async def operation() -> bool:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return True
    key = ("new", "chat", "reply", "video")
    first, second = await asyncio.gather(delivery._singleflight(key, operation), delivery._singleflight(key, operation))
    assert first is True and second is True
    assert calls == 1


def test_delivery_coordinator_is_explicit_not_installed() -> None:
    source = Path(delivery.__file__).read_text(encoding="utf-8")
    assert "def install" not in source
    assert "sys.modules" not in source
    assert "setattr(" not in source
    assert "deliver_new_companions" in source
    assert "deliver_cached_companions" in source
    assert "SourceAudioDeferral" in source
''',
)

# MP3 conversion behavior belongs to services.mp3_conversion, not project-runtime hardening.
write(
    "tests/test_audio_conversion_postcondition.py",
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
    async def fake_probe(path):
        return Path(path) == output
    monkeypatch.setattr(mp3_conversion, "run_cancellable_process", fake_run)
    monkeypatch.setattr(mp3_conversion, "_probe_audio_file", fake_probe)
    # Force re-encode by making source newer than the existing output.
    source.touch()
    assert await mp3_conversion.reencode_mp3_64k_atomic(source, output) is False
    assert output.read_bytes() == old
''',
)

# Current health contract: assert canonical source owners and no legacy facade paths.
write(
    "tests/test_clean_dub_health_contract.py",
    r'''from __future__ import annotations

from pathlib import Path

from handlers import dub_health
from services.dub_worker_release import WORKER_RUNTIME
from services.speech_backends import DEFAULT_BACKEND_ID, default_backend, select_production_backend
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import generic_clean_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import generic_direct_runtime
from tools.voxcpm2 import generic_project_runtime

ROOT = Path(__file__).resolve().parents[1]


def test_quality_contract_accepts_current_source_owned_runtime() -> None:
    ok, detail = dub_health._quality_contract(ROOT)
    assert ok, detail
    assert "runtime-safety" in detail
    assert "recipe-routing" in detail


def test_active_backend_and_source_owned_routes_are_callable() -> None:
    selection = select_production_backend(None, default_backend_id=DEFAULT_BACKEND_ID)
    backend = default_backend()
    assert selection.backend is backend
    assert backend.capabilities().missing() == ()
    assert callable(generic_project_runtime.main)
    assert callable(generic_direct_runtime.main)
    assert callable(repair_runtime._validate_repair_request)
    assert callable(repair_runtime._checkpoint_ready)


def test_runtime_fingerprint_includes_real_source_owners() -> None:
    required = {
        "tools/voxcpm2/clean_runtime_contract.py",
        "tools/voxcpm2/clean_production_core.py",
        "tools/voxcpm2/generic_project_runtime.py",
        "tools/voxcpm2/generic_direct_runtime.py",
        "tools/voxcpm2/generic_clean_audio_repair_runtime.py",
        "tools/voxcpm2/master_direct_russian_only.py",
        "services/speech_backends/voxcpm2.py",
    }
    active = set(clean_runtime_contract._RENDER_MODULES) | set(clean_runtime_contract._RELEASE_MODULES)
    assert required <= active
    assert all("/__init__.py" not in item for item in active)
    assert callable(clean_runtime_contract.build_fingerprints)


def test_worker_runtime_is_directly_owned() -> None:
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    source = Path(dub_health.__file__).read_text(encoding="utf-8")
    assert "from services.dub_worker import build_command" in source
''',
)

# Obsolete tests that asserted the removed mutation mechanism itself. Equivalent
# product behavior is covered above or by existing canonical-owner suites.
for filename in (
    "tests/test_direct_surgical_runtime.py",
    "tests/test_dub_facade_write_through.py",
    "tests/test_dub_health_v60_monolithic_release.py",
    "tests/test_generic_project_runtime_write_through.py",
    "tests/test_gemini_startup_diagnostics.py",
    "tests/test_livedub_cached_delivery_atomicity.py",
    "tests/test_livedub_dual_audio.py",
    "tests/test_livedub_help_runtime.py",
    "tests/test_livedub_new_delivery_atomicity.py",
    "tests/test_livedub_new_delivery_roles.py",
    "tests/test_livedub_publication_error_diagnostics.py",
    "tests/test_project_runtime_hardening.py",
    "tests/test_semantic_prompt_rescue_v47.py",
    "tests/test_semantic_tts_guard_v46.py",
    "tests/test_shorts_factory_quality_publication.py",
):
    Path(filename).unlink(missing_ok=True)

# Process-entrypoint bootstrap test becomes an explicit recipe/worker-owner test.
write(
    "tests/test_dub_quality_process_entrypoints.py",
    r'''from __future__ import annotations

from services.dub_worker import build_command


def test_recipe_routes_all_actions_to_current_source_owners() -> None:
    expected = {
        "render": "tools.voxcpm2.generic_gemini_runtime",
        "render_gemini": "tools.voxcpm2.generic_gemini_runtime",
        "render_direct": "tools.voxcpm2.generic_direct_runtime",
        "render_custom": "tools.voxcpm2.generic_custom_runtime",
        "repair_audio": "tools.voxcpm2.generic_clean_audio_repair_runtime",
    }
    for action, module in expected.items():
        command, spec = build_command("generic_short_v1", action)
        assert spec["module"] == module
        assert command[1:3] == ["-m", module]
''',
)

# Collection guard: retired compatibility module names must not reappear.
write(
    "tests/test_source_owner_retired_modules.py",
    r'''from __future__ import annotations

from pathlib import Path


RETIRED = (
    "services/cut_mode_source_policy.py",
    "services/project_runtime_hardening.py",
    "tools/voxcpm2/generic_direct_checked_runtime.py",
    "tools/voxcpm2/generic_clean_direct_runtime.py",
    "tools/voxcpm2/generic_clean_gemini_runtime.py",
    "tools/voxcpm2/generic_short_runtime.py",
    "tools/voxcpm2/preflight_json_protocol.py",
    "tools/voxcpm2/master_monolithic_mix.py",
)


def test_retired_runtime_surgery_modules_stay_deleted() -> None:
    root = Path(__file__).resolve().parents[1]
    assert not [path for path in RETIRED if (root / path).exists()]


def test_active_routes_do_not_reintroduce_common_surgery_primitives() -> None:
    root = Path(__file__).resolve().parents[1]
    active = (
        "tools/voxcpm2/generic_project_runtime.py",
        "tools/voxcpm2/generic_direct_runtime.py",
        "tools/voxcpm2/generic_gemini_runtime.py",
        "tools/voxcpm2/generic_custom_runtime.py",
        "tools/voxcpm2/master_direct_russian_only.py",
        "tools/voxcpm2/dub_job_preflight.py",
    )
    for relative in active:
        source = (root / relative).read_text(encoding="utf-8")
        assert "sys.modules" not in source, relative
        assert "def install_runtime" not in source, relative
        assert "setattr(module" not in source, relative
''',
)

print("source-owner regression finalizer v3 applied")
