#!/usr/bin/env python3
"""Promote the active clean ready-SRT route into generic_direct_runtime.

This is a branch-only migration tool. It performs one deterministic source-owner
rewrite and refuses partial/legacy results.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "tools/voxcpm2/generic_direct_runtime.py"
WRAPPER = ROOT / "tools/voxcpm2/generic_clean_direct_runtime.py"
BASE = ROOT / "tools/voxcpm2/_generic_clean_direct_runtime_base.py"
UNIVERSAL = ROOT / "tools/voxcpm2/direct_universal_runtime.py"
RECIPE = ROOT / "tools/voxcpm2/recipes/generic_short_v1.json"
HEALTH = ROOT / "handlers/dub_health.py"
RELEASE_HEALTH = ROOT / "services/dub_release_health_v64.py"
CONTRACT = ROOT / "tools/voxcpm2/clean_runtime_contract.py"


def replace_function(text: str, path: Path, name: str, replacement: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            lines[start:end] = [replacement.rstrip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing top-level function {name}")


def remove_function(text: str, path: Path, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            end = node.end_lineno or node.lineno
            del lines[start:end]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing function {name}")


CLEAN_RUN_BLOCK = r'''
def _read_json_value(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = _read_json_value(path)
    return payload if isinstance(payload, dict) else {}


def _same_number(left: Any, right: Any, *, tolerance: float = 1e-6) -> bool:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= tolerance


def _expected_expression(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": str(segment.get("expression_policy") or ""),
        "tier": str(segment.get("expression_tier") or ""),
        "score": segment.get("expression_score"),
        "style_instruction": str(segment.get("style_instruction") or ""),
        "source_prosody": segment.get("source_prosody") or {},
    }


def _legacy_checkpoint_prefix(root: Path, request: dict[str, Any]) -> list[int]:
    segments_payload = _read_json_value(root / "segments_ru_final.json")
    if not isinstance(segments_payload, list) or len(segments_payload) < 2:
        return []
    segments = {
        int(item.get("id")): item
        for item in segments_payload
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }
    if len(segments) != len(segments_payload):
        return []
    segment_work = root / "segment_work"
    checkpoint_paths = sorted((segment_work / "checkpoints").glob("segment_*.json"))
    fitted_dir = segment_work / "segments_fitted"
    if not checkpoint_paths:
        return []
    steps = int(request["steps"]) if request.get("steps") is not None else 16
    cfg = float(request["cfg"]) if request.get("cfg") is not None else 1.8
    base_seed = int(request["base_seed"]) if request.get("base_seed") is not None else 2026072800
    accepted_ids: list[int] = []
    for path in checkpoint_paths:
        payload = _read_json(path)
        signature = payload.get("signature")
        report = payload.get("report")
        if not isinstance(signature, dict) or not isinstance(report, dict):
            return []
        try:
            segment_id = int(report.get("id"))
        except (TypeError, ValueError, OverflowError):
            return []
        segment = segments.get(segment_id)
        if not isinstance(segment, dict):
            return []
        profile = str(segment.get("reference_profile") or "")
        fitted = fitted_dir / f"{segment_id:02d}_{profile}_fitted.wav"
        fit = report.get("fit")
        if (
            not fitted.is_file()
            or fitted.stat().st_size < 4096
            or report.get("renderer_policy") != direct_io.POLICY
            or report.get("selected_raw_pitch_evidence_ok") is not True
            or not isinstance(fit, dict)
            or not _same_number(report.get("start"), segment.get("start"))
            or not _same_number(report.get("end"), segment.get("end"))
            or not _same_number(report.get("tail_guard"), segment.get("tail_guard"))
            or float(fit.get("tempo") or 999.0) > 1.35 + 1e-6
        ):
            return []
        expected_core = {
            "policy": direct_io.POLICY,
            "text": str(segment.get("text") or ""),
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "tail_guard": float(segment["tail_guard"]),
            "start_delay_ms": int(segment.get("start_delay_ms", 0)),
            "reference_profile": profile,
            "expression": _expected_expression(segment),
            "steps": steps,
            "cfg": cfg,
            "base_seed": base_seed,
        }
        for key, expected in expected_core.items():
            actual = signature.get(key)
            if isinstance(expected, float):
                if not _same_number(actual, expected):
                    return []
            elif actual != expected:
                return []
        if not str(signature.get("model_config_sha256") or ""):
            return []
        if not str(signature.get("reference_sha256") or ""):
            return []
        accepted_ids.append(segment_id)
    accepted_ids = sorted(set(accepted_ids))
    if not accepted_ids or accepted_ids != list(range(1, accepted_ids[-1] + 1)):
        return []
    if accepted_ids[-1] >= len(segments_payload):
        return []
    return accepted_ids


def _seed_resumable_clean_marker(root: Path, request: dict[str, Any]) -> None:
    segment_work = root / "segment_work"
    checkpoints = segment_work / "checkpoints"
    if not any(checkpoints.glob("segment_*.json")):
        return
    repo = Path(__file__).resolve().parents[2]
    cpu_python = clean._cpu_python(request)
    archive = Path(str(request.get("vox_archive") or r"C:\AI-Archive\VoxCPM2-paused-RTX3060")).resolve()
    fingerprints = clean.clean_runtime_contract.build_fingerprints(
        repo=repo,
        archive=archive,
        cpu_python=cpu_python,
        backend_id=request.get("speech_backend"),
    )
    expected_direct = {
        "schema_version": 1,
        "policy": "direct-cli-runtime-marker-v1",
        "render_contract_sha256": fingerprints["render_contract_sha256"],
        "cache_length": 4096,
        "python_executable": str(cpu_python.resolve()),
    }
    direct_marker_path = segment_work / "direct_cli_runtime.marker.json"
    direct_marker = _read_json(direct_marker_path)
    migration_ids: list[int] = []
    if direct_marker != expected_direct:
        migration_ids = _legacy_checkpoint_prefix(root, request)
        if not migration_ids:
            return
        direct_marker_path.write_text(
            json.dumps(expected_direct, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    marker = {
        "schema_version": 3,
        "policy": clean.POLICY,
        "runtime_contract_policy": clean.clean_runtime_contract.POLICY,
        "render_contract_sha256": fingerprints["render_contract_sha256"],
        "release_contract_sha256": fingerprints["release_contract_sha256"],
        "segment_qa_passed": False,
        "release_complete": False,
        "checkpoint_resume_provisional": True,
    }
    if migration_ids:
        marker.update(
            checkpoint_resume_migration="validated-late-prefix-after-tempo-policy-v1",
            adopted_checkpoint_ids=migration_ids,
        )
    segment_work.mkdir(parents=True, exist_ok=True)
    (segment_work / "clean_production.marker.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    if migration_ids:
        log(f"восстановлен проверенный checkpoint-prefix 1–{migration_ids[-1]}")
    else:
        log("совместимые fingerprinted checkpoints сохранены для продолжения")


def _renderer_failure_detail(root: Path) -> str:
    payload = _read_json(root / "segment_work" / "direct_renderer_failure.json")
    message = str(payload.get("message") or "").strip()
    error_type = str(payload.get("error_type") or "RuntimeError").strip()
    return f"{error_type}: {message}" if message else ""


def _run_speech_and_master(
    *,
    root: Path,
    request: dict[str, Any],
    source: Path,
    cues: list[Any],
    duration: float,
    segments_json: Path,
    final_mixed: Path,
    final_russian: Path,
) -> Path:
    settings = clean.clean_runtime_contract.normalize_settings(request, duration=duration)
    backend = clean.get_backend(request.get("speech_backend") or clean.DEFAULT_BACKEND_ID)
    if backend.backend_id == "voxcpm2":
        repo = Path(__file__).resolve().parents[2]
        runtime = backend.runtime_paths(repo, request)
        model_path = backend.discover_model(runtime.archive_root)
        model_config = model_path / "config.json"
        if not model_config.is_file():
            raise RuntimeError(f"Не найден config.json выбранной TTS-модели: {model_config}")
        segments_payload = _read_json_value(segments_json)
        if not isinstance(segments_payload, list):
            raise RuntimeError("segments_ru_final.json повреждён до timing preflight.")
        speech_options = request.get("speech_options") or {}
        if not isinstance(speech_options, dict):
            raise RuntimeError("speech_options должен быть JSON-объектом.")
        context = {
            "policy": "voxcpm2-universal-production-hardening-v1",
            "backend": backend.backend_id,
            "adapter_policy": backend.adapter_policy,
            "cfg": float(settings["cfg"]),
            "steps": int(settings["steps"]),
            "base_seed": int(settings["base_seed"]),
            "max_tempo": float(direct_io.MAX_TEMPO),
            "model_config_sha256": direct_io.sha256_file(model_config),
            "speech_model_profile": str(request.get("speech_model_profile") or ""),
            "speech_profile_fingerprint": str(request.get("speech_profile_fingerprint") or ""),
            "speech_options": speech_options,
        }
        work_dir = root / "segment_work"
        direct_timing_guard.write_signature_context(work_dir, context)
        report = direct_timing_guard.run_pre_model_guard(
            segments_payload,
            work_dir=work_dir,
            max_tempo=direct_io.MAX_TEMPO,
            signature_context=context,
        )
        log(
            "universal timing preflight passed before voice references/model: "
            f"warnings={report.get('warning_ids') or []}"
        )
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source,
        cues=cues,
        duration=duration,
        reference_dir=root / "references",
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _built, reference_detail = controlled_reference_gate.build_or_keep_calm(
        source=source,
        segments=planned,
        output=composite,
        identity_reference=extended,
    )
    log("source-guided emotional arc prepared; user SRT text preserved; " + reference_detail)
    _seed_resumable_clean_marker(root, request)
    try:
        return clean.render_and_master(
            root=root,
            request=request,
            source=source,
            duration=duration,
            segments_json=segments_json,
            extended_reference=extended,
            composite_reference=composite,
            final_mixed=final_mixed,
            final_russian=final_russian,
            force_fresh=False,
        )
    except RuntimeError as exc:
        detail = _renderer_failure_detail(root)
        if detail and "завершился с кодом" in str(exc):
            raise RuntimeError(f"Прямой VoxCPM2 renderer: {detail}") from exc
        raise
'''

DIRECT_SEGMENTS = r'''
def _build_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    return semantic_block_runtime.build_direct_segments(
        groups,
        delay_ms=delay_ms,
        duration=duration,
    )
'''


def main() -> int:
    for path in (OWNER, WRAPPER, BASE, UNIVERSAL, RECIPE, HEALTH, RELEASE_HEALTH, CONTRACT):
        if not path.is_file():
            raise RuntimeError(f"missing migration input: {path}")
    owner = OWNER.read_text(encoding="utf-8")
    if "CLEAN_DIRECT_ROUTE_POLICY" in owner:
        raise RuntimeError("generic direct owner already migrated")
    owner = owner.replace("import json\n", "import json\nimport math\n", 1)
    owner = owner.replace("from tools.voxcpm2 import generic_short_runtime as hardened\n", "")
    import_anchor = "from tools.voxcpm2 import generic_short_production as pipeline\n"
    clean_imports = (
        "from tools.voxcpm2 import clean_production_core as clean\n"
        "from tools.voxcpm2 import clean_request_settings\n"
        "from tools.voxcpm2 import clean_source_download\n"
        "from tools.voxcpm2 import continuous_reference_policy\n"
        "from tools.voxcpm2 import controlled_reference_gate\n"
        "from tools.voxcpm2 import direct_max_quality_io as direct_io\n"
        "from tools.voxcpm2 import direct_timing_guard\n"
        "from tools.voxcpm2 import expressive_continuity\n"
        "from tools.voxcpm2 import semantic_block_runtime\n"
    )
    if import_anchor not in owner:
        raise RuntimeError("generic direct import anchor changed")
    owner = owner.replace(import_anchor, clean_imports + import_anchor, 1)
    owner = owner.replace(
        "\ndef _run_speech_and_master(**kwargs: Any) -> Path:\n    \"\"\"Generic engine hook; the selected route may replace this before main().\"\"\"\n    return _run_voxcpm_and_master(**kwargs)\n",
        "\nCLEAN_DIRECT_ROUTE_POLICY = \"source-owned-clean-direct-v1\"\n\n" + CLEAN_RUN_BLOCK.strip() + "\n",
        1,
    )
    owner = replace_function(owner, OWNER, "_build_direct_segments", DIRECT_SEGMENTS)
    owner = owner.replace("    hardened.install_runtime_adapters()\n", "    log(\"clean direct adapters source-owned; TTS guard disabled\")\n", 1)
    owner = owner.replace(
        "    metadata = hardened.download_source(source_url, source)\n",
        "    metadata = clean_source_download.download_source(source_url, source)\n",
        1,
    )
    owner = owner.replace(
        "    groups = group_srt_cues(normalized_cues)\n",
        "    groups = semantic_block_runtime.group_ready_srt(normalized_cues)\n",
        1,
    )
    owner = owner.replace(
        "    delay_ms = int(request.get(\"russian_delay_ms\") or 420)\n",
        "    delay_ms = clean_request_settings.russian_delay_ms(request)\n",
        1,
    )
    manifest_anchor = '    save_json(output_dir / "manifest.json", manifest)\n'
    if manifest_anchor not in owner:
        raise RuntimeError("manifest save anchor changed")
    owner = owner.replace(
        manifest_anchor,
        manifest_anchor + "    clean_request_settings.repair_manifest(root, request)\n",
        1,
    )
    forbidden_owner = (
        "hardened.install_runtime_adapters()",
        "hardened.download_source(",
        "production.",
        "install_generic_preflight",
        "exec(compile(",
        "generic_clean_direct_runtime",
        "_generic_clean_direct_runtime_base",
    )
    bad = [token for token in forbidden_owner if token in owner]
    if bad:
        raise RuntimeError(f"generic direct owner retained legacy tokens: {bad}")
    for required in (
        "semantic_block_runtime.group_ready_srt",
        "semantic_block_runtime.build_direct_segments",
        "clean_source_download.download_source",
        "direct_timing_guard.run_pre_model_guard",
        "continuous_reference_policy.build_calm_references",
        "controlled_reference_gate.build_or_keep_calm",
        "clean.render_and_master(",
        "clean_request_settings.russian_delay_ms(request)",
        "clean_request_settings.repair_manifest(root, request)",
    ):
        if required not in owner:
            raise RuntimeError(f"generic direct owner missing {required}")
    ast.parse(owner, filename=str(OWNER))
    OWNER.write_text(owner, encoding="utf-8")

    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    action = recipe["actions"]["render_direct"]
    if action.get("module") != "tools.voxcpm2.generic_clean_direct_runtime":
        raise RuntimeError(f"unexpected direct recipe route: {action.get('module')!r}")
    action["module"] = "tools.voxcpm2.generic_direct_runtime"
    RECIPE.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    health = HEALTH.read_text(encoding="utf-8")
    health = health.replace('"direct": voxcpm / "generic_clean_direct_runtime.py"', '"direct": voxcpm / "generic_direct_runtime.py"')
    health = health.replace(
        'and all(\n                "hardened.download_source = clean_source_download.download_source" in text[name]\n                and "hardened.pipeline.download_source = clean_source_download.download_source" in text[name]\n                for name in source_route_names\n            )',
        'and all(\n                "hardened.download_source = clean_source_download.download_source" in text[name]\n                and "hardened.pipeline.download_source = clean_source_download.download_source" in text[name]\n                for name in ("gemini", "custom")\n            )\n            and "clean_source_download.download_source(source_url, source)" in text["direct"]',
    )
    health = health.replace(
        '"tools.voxcpm2.generic_clean_direct_runtime" in direct_text',
        '"tools.voxcpm2.generic_direct_runtime" in direct_text',
    )
    if "generic_clean_direct_runtime" in health:
        raise RuntimeError("dub health still references clean direct wrapper")
    ast.parse(health, filename=str(HEALTH))
    HEALTH.write_text(health, encoding="utf-8")

    release = RELEASE_HEALTH.read_text(encoding="utf-8")
    release = release.replace('voxcpm / "generic_clean_direct_runtime.py"', 'voxcpm / "generic_direct_runtime.py"')
    release = release.replace(
        '"production._run_speech_and_master = _run_clean_voxcpm_and_master",',
        '"clean.render_and_master(",',
    )
    release = release.replace('"clean.build_direct_segments(",', '"semantic_block_runtime.build_direct_segments(",')
    if "generic_clean_direct_runtime" in release:
        raise RuntimeError("release health still references clean direct wrapper")
    ast.parse(release, filename=str(RELEASE_HEALTH))
    RELEASE_HEALTH.write_text(release, encoding="utf-8")

    contract = CONTRACT.read_text(encoding="utf-8")
    contract = contract.replace("'tools/voxcpm2/generic_clean_direct_runtime.py', ", "")
    contract = contract.replace("'tools/voxcpm2/_generic_clean_direct_runtime_base.py', ", "")
    if "generic_clean_direct_runtime.py" in contract or "_generic_clean_direct_runtime_base.py" in contract:
        raise RuntimeError("runtime fingerprint still references retired direct wrapper")
    ast.parse(contract, filename=str(CONTRACT))
    CONTRACT.write_text(contract, encoding="utf-8")

    universal = UNIVERSAL.read_text(encoding="utf-8")
    universal = remove_function(universal, UNIVERSAL, "install_generic_preflight")
    universal = universal.replace("'install_generic_preflight', ", "").replace(", 'install_generic_preflight'", "")
    universal = universal.replace("'install_generic_preflight'", "")
    if "install_generic_preflight" in universal:
        raise RuntimeError("generic preflight installer survived")
    ast.parse(universal, filename=str(UNIVERSAL))
    UNIVERSAL.write_text(universal, encoding="utf-8")

    WRAPPER.unlink()
    BASE.unlink()

    for path in (OWNER, RECIPE, HEALTH, RELEASE_HEALTH, CONTRACT, UNIVERSAL):
        if not path.is_file():
            raise RuntimeError(f"migration output missing: {path}")
    production_roots = [ROOT / p for p in ("tools/voxcpm2", "services", "handlers", "core", "pipelines")]
    blockers: list[str] = []
    retired = ("generic_clean_direct_runtime", "_generic_clean_direct_runtime_base", "install_generic_preflight")
    for base_dir in production_roots:
        for path in base_dir.rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(token in text for token in retired):
                blockers.append(path.relative_to(ROOT).as_posix())
    if blockers:
        raise RuntimeError("retired direct route references remain: " + ", ".join(sorted(set(blockers))))
    print("generic direct route source-owned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
