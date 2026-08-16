#!/usr/bin/env python3
"""Replace Gemini/custom module rebinding with an explicit ProjectRoute contract."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "tools/voxcpm2/generic_project_runtime.py"
GEMINI = ROOT / "tools/voxcpm2/generic_gemini_runtime.py"
CLEAN_GEMINI = ROOT / "tools/voxcpm2/generic_clean_gemini_runtime.py"
CLEAN_CUSTOM = ROOT / "tools/voxcpm2/generic_clean_custom_runtime.py"
CUSTOM = ROOT / "tools/voxcpm2/generic_custom_runtime.py"
RECIPE = ROOT / "tools/voxcpm2/recipes/generic_short_v1.json"
CONTRACT = ROOT / "tools/voxcpm2/clean_runtime_contract.py"
HEALTH = ROOT / "handlers/dub_health.py"
RELEASE_HEALTH = ROOT / "services/dub_release_health_v64.py"

ROUTE_BLOCK = r'''
@dataclass(frozen=True)
class ProjectRoute:
    download_source: Callable[..., dict[str, Any]]
    acquire_transcript: Callable[..., tuple[list[pipeline.Cue], str, str]]
    group_source: Callable[[list[pipeline.Cue]], list[dict[str, Any]]]
    translate_groups: Callable[..., list[dict[str, Any]]]
    validate_translation: Callable[[Any, list[dict[str, Any]]], list[dict[str, Any]]]
    build_render_segments: Callable[..., tuple[list[dict[str, Any]], list[pipeline.Cue]]]
    run_speech_and_master: Callable[..., Path]
    delay_ms: Callable[[dict[str, Any]], int]
    finalize: Callable[[Path, dict[str, Any]], None]


def _default_delay_ms(request: dict[str, Any]) -> int:
    return int(request.get("russian_delay_ms") or 420)


def _no_finalize(root: Path, request: dict[str, Any]) -> None:
    del root, request


def default_project_route() -> ProjectRoute:
    return ProjectRoute(
        download_source=pipeline.download_source,
        acquire_transcript=acquire_transcript,
        group_source=_source_groups,
        translate_groups=translate_groups_max,
        validate_translation=_validate_translation_payload,
        build_render_segments=_build_render_segments,
        run_speech_and_master=_run_speech_and_master,
        delay_ms=_default_delay_ms,
        finalize=_no_finalize,
    )
'''

GEMINI_MAIN = r'''
def _clean_source_groups(cues: list[pipeline.Cue]) -> list[dict[str, Any]]:
    groups = clean.group_source_cues(cues)
    for group in groups:
        group["source"] = group.pop("english")
    return groups


def _acquire_transcript_clean(*args: Any, **kwargs: Any) -> tuple[list[pipeline.Cue], str, str]:
    kwargs["manual_vtt_parser"] = parse_creator_vtt_preserving_text
    cues, caption_origin, source_language = production.acquire_transcript(*args, **kwargs)
    metadata = kwargs.get("metadata")
    if metadata is None and len(args) >= 4:
        metadata = args[3]
    if isinstance(metadata, dict):
        language = str(source_language or "unknown")
        metadata["language"] = language
        metadata["source_language"] = language
    return cues, caption_origin, source_language


def _run_clean_speech_and_master(
    *, root: Path, request: dict[str, Any], source: Path, cues: list[Any],
    duration: float, segments_json: Path, final_mixed: Path, final_russian: Path,
) -> Path:
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source, cues=cues, duration=duration, reference_dir=root / "references"
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _built, detail = controlled_reference_gate.build_or_keep_calm(
        source=source, segments=planned, output=composite, identity_reference=extended
    )
    production.log("source-guided emotional arc prepared; Russian text preserved; " + detail)
    return clean.render_and_master(
        root=root, request=request, source=source, duration=duration,
        segments_json=segments_json, extended_reference=extended,
        composite_reference=composite, final_mixed=final_mixed,
        final_russian=final_russian, force_fresh=True,
    )


def _finalize_clean_gemini(root: Path, request: dict[str, Any]) -> None:
    clean_request_settings.repair_manifest(root, request)
    validate_completed_outputs(root)


def main() -> None:
    route = production.ProjectRoute(
        download_source=clean_source_download.download_source,
        acquire_transcript=_acquire_transcript_clean,
        group_source=_clean_source_groups,
        translate_groups=expressive_translation.translate_groups,
        validate_translation=production._validate_translation_payload,
        build_render_segments=clean.build_render_segments,
        run_speech_and_master=_run_clean_speech_and_master,
        delay_ms=clean_request_settings.russian_delay_ms,
        finalize=_finalize_clean_gemini,
    )
    production.main(route)
    production.log("=== GEMINI MAX SOURCE-OWNED OUTPUT CONTRACT: OK ===")
'''

CUSTOM_FILE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Source-owned custom-translation Dub Studio entrypoint."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_production_core as clean
from tools.voxcpm2 import clean_request_settings
from tools.voxcpm2 import clean_source_download
from tools.voxcpm2 import continuous_reference_policy
from tools.voxcpm2 import controlled_reference_gate
from tools.voxcpm2 import expressive_continuity
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import strict_translation_payload

POLICY = "source-owned-clean-custom-v1"


def _clean_source_groups(cues: list[Any]) -> list[dict[str, Any]]:
    groups = clean.group_source_cues(cues)
    for group in groups:
        group["source"] = group.pop("english")
    return groups


def _run_clean_speech_and_master(
    *, root: Path, request: dict[str, Any], source: Path, cues: list[Any],
    duration: float, segments_json: Path, final_mixed: Path, final_russian: Path,
) -> Path:
    extended, composite = continuous_reference_policy.build_calm_references(
        source=source, cues=cues, duration=duration, reference_dir=root / "references"
    )
    planned = expressive_continuity.plan_json(
        source=source,
        segments_path=segments_json,
        duration=duration,
        report_path=root / "output" / "expressive_continuity.json",
    )
    _built, detail = controlled_reference_gate.build_or_keep_calm(
        source=source, segments=planned, output=composite, identity_reference=extended
    )
    production.log("source-guided emotional arc prepared; custom text preserved; " + detail)
    return clean.render_and_master(
        root=root, request=request, source=source, duration=duration,
        segments_json=segments_json, extended_reference=extended,
        composite_reference=composite, final_mixed=final_mixed,
        final_russian=final_russian, force_fresh=True,
    )


def _finalize(root: Path, request: dict[str, Any]) -> None:
    clean_request_settings.repair_manifest(root, request)


def main() -> None:
    route = production.ProjectRoute(
        download_source=clean_source_download.download_source,
        acquire_transcript=production.acquire_transcript,
        group_source=_clean_source_groups,
        translate_groups=production.translate_groups_max,
        validate_translation=strict_translation_payload.validate_full,
        build_render_segments=clean.build_render_segments,
        run_speech_and_master=_run_clean_speech_and_master,
        delay_ms=clean_request_settings.russian_delay_ms,
        finalize=_finalize,
    )
    production.main(route)


if __name__ == "__main__":
    main()
'''


def replace_function(text: str, path: Path, name: str, replacement: str) -> str:
    tree = ast.parse(text, filename=str(path)); lines=text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==name:
            lines[node.lineno-1:node.end_lineno]=[replacement.rstrip()+"\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing {name}")


def main() -> int:
    for path in (PROJECT,GEMINI,CLEAN_GEMINI,CLEAN_CUSTOM,RECIPE,CONTRACT,HEALTH,RELEASE_HEALTH):
        if not path.is_file(): raise RuntimeError(f"missing input: {path}")

    project=PROJECT.read_text(encoding="utf-8")
    project=project.replace("import argparse\n", "import argparse\nfrom collections.abc import Callable\nfrom dataclasses import dataclass\n",1)
    # Explicit manual-caption parser dependency instead of rebinding parse_manual_vtt.
    project=project.replace(
        "def _download_track(url: str, source_dir: Path, *, kind: str, language: str) -> list[pipeline.Cue]:",
        "def _download_track(url: str, source_dir: Path, *, kind: str, language: str, manual_vtt_parser: Callable[[Path], list[pipeline.Cue]] | None = None) -> list[pipeline.Cue]:",
        1,
    )
    project=project.replace(
        '        cues = parse_manual_vtt(path) if kind == "manual" else pipeline.parse_vtt(path)',
        '        parser = manual_vtt_parser or parse_manual_vtt\n        cues = parser(path) if kind == "manual" else pipeline.parse_vtt(path)',
        1,
    )
    project=project.replace(
        "    duration: float,\n) -> tuple[list[pipeline.Cue], str, str]:",
        "    duration: float,\n    manual_vtt_parser: Callable[[Path], list[pipeline.Cue]] | None = None,\n) -> tuple[list[pipeline.Cue], str, str]:",
        1,
    )
    project=project.replace(
        "cues = _download_track(source_url, source_dir, kind=preferred_kind, language=preferred_language)",
        "cues = _download_track(source_url, source_dir, kind=preferred_kind, language=preferred_language, manual_vtt_parser=manual_vtt_parser)",
        1,
    )
    project=project.replace(
        'cues = _download_track(source_url, source_dir, kind="automatic", language=used_language)',
        'cues = _download_track(source_url, source_dir, kind="automatic", language=used_language, manual_vtt_parser=manual_vtt_parser)',
        1,
    )
    # Custom text parser takes an explicit validator too.
    project=project.replace(
        "def parse_custom_translation(text: str, groups: list[dict[str, Any]]) -> list[dict[str, Any]]:",
        "def parse_custom_translation(text: str, groups: list[dict[str, Any]], *, validator: Callable[[Any, list[dict[str, Any]]], list[dict[str, Any]]] = _validate_translation_payload) -> list[dict[str, Any]]:",
        1,
    )
    project=project.replace("        return _validate_translation_payload(payload, groups)", "        return validator(payload, groups)", 1)
    # Insert route contract immediately before orchestration main.
    anchor="\ndef main() -> None:\n    pipeline.configure_utf8()\n"
    if anchor not in project: raise RuntimeError("project main anchor changed")
    project=project.replace(anchor, "\n"+ROUTE_BLOCK.strip()+"\n\ndef main(route: ProjectRoute | None = None) -> None:\n    route = route or default_project_route()\n    pipeline.configure_utf8()\n",1)
    replacements=(
        ("metadata = pipeline.download_source(source_url, source)","metadata = route.download_source(source_url, source)"),
        ("cues, caption_origin, source_language = acquire_transcript(","cues, caption_origin, source_language = route.acquire_transcript("),
        ("groups = _source_groups(cues)","groups = route.group_source(cues)"),
        ("translations = translate_groups_max(","translations = route.translate_groups("),
        ("translations = _validate_translation_payload(\n                json.loads(custom_json.read_text(encoding=\"utf-8-sig\")),\n                groups,\n            )","translations = route.validate_translation(\n                json.loads(custom_json.read_text(encoding=\"utf-8-sig\")),\n                groups,\n            )"),
        ("translations = parse_custom_translation(custom_txt.read_text(encoding=\"utf-8-sig\"), groups)","translations = parse_custom_translation(custom_txt.read_text(encoding=\"utf-8-sig\"), groups, validator=route.validate_translation)"),
        ("delay_ms = int(request.get(\"russian_delay_ms\") or 420)","delay_ms = route.delay_ms(request)"),
        ("render_segments, russian_cues = _build_render_segments(","render_segments, russian_cues = route.build_render_segments("),
        ("russian_timeline = _run_speech_and_master(","russian_timeline = route.run_speech_and_master("),
    )
    for old,new in replacements:
        if old not in project: raise RuntimeError(f"project route anchor missing: {old[:70]}")
        project=project.replace(old,new,1)
    prepare_marker='        save_json(manifest_path, base_manifest)\n        log("=== ПОДГОТОВКА ГОТОВА: ОЖИДАЕТСЯ ПЕРЕВОД ПОЛЬЗОВАТЕЛЯ ===")'
    if prepare_marker not in project: raise RuntimeError("prepare finalize anchor changed")
    project=project.replace(prepare_marker,'        save_json(manifest_path, base_manifest)\n        route.finalize(root, request)\n        log("=== ПОДГОТОВКА ГОТОВА: ОЖИДАЕТСЯ ПЕРЕВОД ПОЛЬЗОВАТЕЛЯ ===")',1)
    completed_marker='    save_json(manifest_path, base_manifest)\n    log("=== ГОТОВО ===")'
    if completed_marker not in project: raise RuntimeError("completed finalize anchor changed")
    project=project.replace(completed_marker,'    save_json(manifest_path, base_manifest)\n    route.finalize(root, request)\n    log("=== ГОТОВО ===")',1)
    ast.parse(project,filename=str(PROJECT)); PROJECT.write_text(project,encoding="utf-8")

    gemini=GEMINI.read_text(encoding="utf-8")
    # Remove installer imports and add clean source owners.
    for line in (
        "from tools.voxcpm2 import dub_quality_v4\n",
        "from tools.voxcpm2 import semantic_tts_guard as legacy_semantic_guard\n",
        "from tools.voxcpm2 import semantic_tts_guard_v4\n",
    ): gemini=gemini.replace(line,"")
    import_anchor="from tools.voxcpm2 import generic_project_runtime as production\n"
    clean_imports=(
        "from tools.voxcpm2 import clean_production_core as clean\n"
        "from tools.voxcpm2 import clean_request_settings\n"
        "from tools.voxcpm2 import clean_source_download\n"
        "from tools.voxcpm2 import continuous_reference_policy\n"
        "from tools.voxcpm2 import controlled_reference_gate\n"
        "from tools.voxcpm2 import expressive_continuity\n"
        "from tools.voxcpm2 import expressive_translation\n"
    )
    gemini=gemini.replace(import_anchor,clean_imports+import_anchor,1)
    gemini=replace_function(gemini,GEMINI,"main",GEMINI_MAIN)
    # Remove obsolete disable helper if present.
    try: gemini=replace_function(gemini,GEMINI,"_disable_legacy_guard_install","")
    except RuntimeError: pass
    for token in ("install_gemini_quality", "semantic_tts_guard_v4.install", "legacy_semantic_guard.install =", "production.parse_manual_vtt ="):
        if token in gemini: raise RuntimeError(f"Gemini owner retained mutation: {token}")
    ast.parse(gemini,filename=str(GEMINI)); GEMINI.write_text(gemini,encoding="utf-8")

    CUSTOM.write_text(CUSTOM_FILE,encoding="utf-8"); ast.parse(CUSTOM_FILE,filename=str(CUSTOM))

    recipe=json.loads(RECIPE.read_text(encoding="utf-8"))
    for name in ("render","render_gemini"):
        if recipe["actions"][name].get("module") != "tools.voxcpm2.generic_clean_gemini_runtime": raise RuntimeError(f"unexpected {name} route")
        recipe["actions"][name]["module"]="tools.voxcpm2.generic_gemini_runtime"
    for name in ("prepare_custom","render_custom"):
        if recipe["actions"][name].get("module") != "tools.voxcpm2.generic_clean_custom_runtime": raise RuntimeError(f"unexpected {name} route")
        recipe["actions"][name]["module"]="tools.voxcpm2.generic_custom_runtime"
    RECIPE.write_text(json.dumps(recipe,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    contract=CONTRACT.read_text(encoding="utf-8")
    contract=contract.replace("'tools/voxcpm2/generic_clean_gemini_runtime.py', ","").replace("'tools/voxcpm2/generic_clean_custom_runtime.py', ","")
    if "'tools/voxcpm2/generic_custom_runtime.py'" not in contract:
        contract=contract.replace("'tools/voxcpm2/generic_gemini_runtime.py', ","'tools/voxcpm2/generic_gemini_runtime.py', 'tools/voxcpm2/generic_custom_runtime.py', ",1)
    ast.parse(contract,filename=str(CONTRACT)); CONTRACT.write_text(contract,encoding="utf-8")

    health=HEALTH.read_text(encoding="utf-8")
    health=health.replace('"gemini": voxcpm / "generic_clean_gemini_runtime.py"','"gemini": voxcpm / "generic_gemini_runtime.py"')
    health=health.replace('"custom": voxcpm / "generic_clean_custom_runtime.py"','"custom": voxcpm / "generic_custom_runtime.py"')
    health=health.replace('"tools.voxcpm2.generic_clean_gemini_runtime" in gemini_text','"tools.voxcpm2.generic_gemini_runtime" in gemini_text')
    health=health.replace('"hardened.download_source = clean_source_download.download_source" in text[name]\n                and "hardened.pipeline.download_source = clean_source_download.download_source" in text[name]','"clean_source_download.download_source" in text[name]')
    health=health.replace('"production.translate_groups_max = expressive_translation.translate_groups" in text["gemini"]','"translate_groups=expressive_translation.translate_groups" in text["gemini"]')
    health=health.replace('"production._validate_translation_payload = strict_translation_payload.validate_full" in text["custom"]','"validate_translation=strict_translation_payload.validate_full" in text["custom"]')
    health=health.replace('"production.acquire_transcript = _acquire_transcript_with_actual_language" in text["gemini"]','"acquire_transcript=_acquire_transcript_clean" in text["gemini"]')
    health=health.replace('"production.parse_manual_vtt = checked.parse_creator_vtt_preserving_text" in text["gemini"]','"manual_vtt_parser\"] = parse_creator_vtt_preserving_text" in text["gemini"]')
    ast.parse(health,filename=str(HEALTH)); HEALTH.write_text(health,encoding="utf-8")

    release=RELEASE_HEALTH.read_text(encoding="utf-8")
    release=release.replace('voxcpm / "generic_clean_gemini_runtime.py"','voxcpm / "generic_gemini_runtime.py"').replace('voxcpm / "generic_clean_custom_runtime.py"','voxcpm / "generic_custom_runtime.py"')
    ast.parse(release,filename=str(RELEASE_HEALTH)); RELEASE_HEALTH.write_text(release,encoding="utf-8")

    CLEAN_GEMINI.unlink(); CLEAN_CUSTOM.unlink()
    blockers=[]
    for root_name in ("tools/voxcpm2","services","handlers","core","pipelines"):
        for path in (ROOT/root_name).rglob("*.py"):
            if "tests" in path.parts: continue
            text=path.read_text(encoding="utf-8",errors="replace")
            if "generic_clean_gemini_runtime" in text or "generic_clean_custom_runtime" in text:
                blockers.append(path.relative_to(ROOT).as_posix())
    if blockers: raise RuntimeError("retired clean route refs remain: "+", ".join(sorted(set(blockers))))
    print("Gemini/custom routes source-owned through ProjectRoute")
    return 0

if __name__=="__main__": raise SystemExit(main())
