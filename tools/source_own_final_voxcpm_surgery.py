#!/usr/bin/env python3
"""Final source-owner cleanup for remaining VoxCPM runtime surgery."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VOX = ROOT / "tools/voxcpm2"
CONTRACT = VOX / "clean_runtime_contract.py"

DEAD = (
    VOX / "examples/john_piper_z20py4yqhyq/voxcpm2_cpu_semantic_wrapper.py",
    VOX / "voxcpm2_cpu_semantic_wrapper.py",
    VOX / "semantic_tts_guard_v46.py",
    VOX / "professional_segmentation_v45.py",
)


def remove_function(text: str, path: Path, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start:node.end_lineno]
            return "".join(lines)
    raise RuntimeError(f"{path}: missing top-level function {name}")


def _production_refs(token: str, exclude: set[Path]) -> list[str]:
    refs: list[str] = []
    for root_name in ("tools/voxcpm2", "services", "handlers", "core", "pipelines"):
        for path in (ROOT / root_name).rglob("*.py"):
            if path in exclude or "tests" in path.parts:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel == "services/dub_release_health_v64.py":
                continue
            if rel.startswith(("tools/source_own_", "tools/refactor_", "tools/prune_", "tools/runtime_", "tools/classify_", "tools/flatten_", "tools/rewrite_")):
                continue
            if token in path.read_text(encoding="utf-8", errors="replace"):
                refs.append(rel)
    return sorted(set(refs))


def main() -> int:
    # semantic_tts_guard: keep pure ASR/QA/retry helpers, delete module interception.
    guard = VOX / "semantic_tts_guard.py"
    text = guard.read_text(encoding="utf-8")
    text = remove_function(text, guard, "install")
    text = remove_function(text, guard, "_run_guarded_synth")
    text = remove_function(text, guard, "_is_voxcpm_synth")
    text = text.replace('_WRAPPER_NAME = "voxcpm2_cpu_semantic_wrapper.py"\n', '')
    # Proxy existed only for install(). Remove it too.
    tree = ast.parse(text, filename=str(guard)); lines=text.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node, ast.ClassDef) and node.name == "GuardedSubprocessProxy":
            start=node.lineno-1
            while start>0 and not lines[start-1].strip(): start-=1
            del lines[start:node.end_lineno]
            break
    text="".join(lines)
    for token in ("sys.modules", "setattr(module", "pipeline.subprocess =", "def install("):
        if token in text: raise RuntimeError(f"semantic guard retained surgery: {token}")
    ast.parse(text, filename=str(guard)); guard.write_text(text, encoding="utf-8")

    guard4 = VOX / "semantic_tts_guard_v4.py"
    text = guard4.read_text(encoding="utf-8")
    text = remove_function(text, guard4, "install")
    tree=ast.parse(text, filename=str(guard4)); lines=text.splitlines(keepends=True)
    for node in reversed(tree.body):
        if isinstance(node, ast.ClassDef) and node.name == "QualityV4SubprocessProxy":
            start=node.lineno-1
            while start>0 and not lines[start-1].strip(): start-=1
            del lines[start:node.end_lineno]
            break
    text="".join(lines)
    # Repair __all__ from the old installer export.
    text=text.replace('    "QualityV4SubprocessProxy",\n', '').replace('    "install",\n', '')
    for token in ("sys.modules", "setattr(module", "pipeline.subprocess =", "def install("):
        if token in text: raise RuntimeError(f"semantic v4 retained surgery: {token}")
    ast.parse(text, filename=str(guard4)); guard4.write_text(text, encoding="utf-8")

    # dub_quality_v4: useful pure grouping/reference functions stay; installers go.
    quality = VOX / "dub_quality_v4.py"
    text=quality.read_text(encoding="utf-8")
    for name in ("install_gemini_quality", "install_direct_quality"):
        text=remove_function(text, quality, name)
    text=text.replace('    "install_direct_quality",\n','').replace('    "install_gemini_quality",\n','')
    for token in ("pipeline.group_cues =", "pipeline.build_reference =", "production._build_render_segments =", "production.acquire_transcript =", "production.group_srt_cues ="):
        if token in text: raise RuntimeError(f"dub quality retained installer write: {token}")
    ast.parse(text, filename=str(quality)); quality.write_text(text, encoding="utf-8")

    # independent QA recovery is invoked explicitly by clean core; installer mutation is dead.
    retry = VOX / "independent_qa_retry.py"
    text=retry.read_text(encoding="utf-8")
    text=remove_function(text, retry, "install")
    text=text.replace('    "install",\n','')
    for token in ("clean_production_core.render_and_master =", "_independent_qa_retry_policy =", "_independent_qa_retry_original ="):
        if token in text: raise RuntimeError(f"independent QA retained mutation: {token}")
    ast.parse(text, filename=str(retry)); retry.write_text(text, encoding="utf-8")

    # Move stable fit-aware candidate policy into canonical direct CLI.
    cli=VOX / "direct_max_quality_cli.py"
    text=cli.read_text(encoding="utf-8")
    text=text.replace("    MAX_TEMPO,\n    SPEECH_SLOT_POLICY,", "    MAX_TEMPO,\n    PREFERRED_MAX_TEMPO,\n    SPEECH_SLOT_POLICY,", 1)
    text=text.replace("    candidate_score,\n", "    candidate_score as _base_candidate_score,\n", 1)
    anchor="BASE_CANDIDATE_ATTEMPTS = 3\n"
    fit_block='''def _tempo_policy_penalty(duration: float, speech_slot: float) -> float:\n    ratio = float(duration) / max(0.1, float(speech_slot))\n    if ratio <= PREFERRED_MAX_TEMPO:\n        return 0.0\n    return 90.0 + (ratio - PREFERRED_MAX_TEMPO) * 400.0\n\n\ndef candidate_score(\n    candidate: dict[str, Any],\n    speech_slot: float,\n    reference_voice: dict[str, Any],\n) -> float:\n    \"\"\"Score with source-owned preference against avoidable hard fitting.\"\"\"\n    base = float(_base_candidate_score(candidate, speech_slot, reference_voice))\n    penalty = _tempo_policy_penalty(float(candidate.get(\"duration\") or 0.0), speech_slot)\n    candidate[\"tempo_preference_penalty\"] = float(penalty)\n    candidate[\"required_tempo_estimate\"] = float(candidate.get(\"duration\") or 0.0) / max(0.1, float(speech_slot))\n    return base + penalty\n\n\n'''
    if "def _tempo_policy_penalty(" not in text:
        if anchor not in text: raise RuntimeError("direct CLI candidate anchor changed")
        text=text.replace(anchor, fit_block+anchor,1)
    ast.parse(text, filename=str(cli)); cli.write_text(text, encoding="utf-8")

    stable=VOX / "examples/john_piper_z20py4yqhyq/voxcpm2_cpu_shorts_production.py"
    text=stable.read_text(encoding="utf-8")
    start=text.find("_ORIGINAL_CANDIDATE_SCORE =")
    end=text.find("MARKER_POLICY =")
    if start < 0 or end < 0 or end <= start: raise RuntimeError("stable CLI mutation block changed")
    text=text[:start] + "main = _direct_cli.main\n\n" + text[end:]
    text=text.replace("from tools.voxcpm2.direct_max_quality_io import (\n    MAX_TEMPO as HARD_MAX_TEMPO,\n    PREFERRED_MAX_TEMPO,\n)\n", "")
    for token in ("_direct_cli.candidate_score =", "_direct_cli.MAX_TEMPO ="):
        if token in text: raise RuntimeError(f"stable CLI retained mutation: {token}")
    ast.parse(text, filename=str(stable)); stable.write_text(text, encoding="utf-8")

    # Clean audio repair: base main is the real owner; late stricter helpers override by name.
    repair=VOX / "generic_clean_audio_repair_runtime.py"
    text=repair.read_text(encoding="utf-8")
    marker="_BASE_ALL = tuple(globals().get('__all__', ()))\n"
    if marker not in text: raise RuntimeError("clean repair extension anchor changed")
    text=text.replace(marker, "_source_main = main\n\n"+marker,1)
    text=text.replace("    segments = legacy_repair._load_segments(segments_path)\n", "    segments = _load_segments(segments_path)\n",1)
    text=text.replace("    main()\n\n_next_seed = _next_seed", "    _source_main()\n\n_next_seed = _next_seed",1)
    text=text.replace("legacy_repair._load_segments = _load_segments\n\n", "")
    all_start=text.rfind("__all__ = sorted(")
    if all_start >= 0:
        text=text[:all_start] + '''__all__ = [\n    "_checkpoint_ready", "_delay_evidence", "_dominant_segment_delay",\n    "_load_segments", "_next_seed", "_strict_ids", "_validate_repair_request",\n    "_validated_sha256", "_update_manifest", "main",\n]\n'''
    for token in ("legacy_repair._load_segments =", "def main() -> None:\n    project_id", "dir(_legacy)"):
        if token in text and token != "def main() -> None:\n    project_id":
            raise RuntimeError(f"clean repair retained legacy extension: {token}")
    # Ensure the final main calls the saved source owner and never itself.
    if "_source_main()" not in text: raise RuntimeError("clean repair source-main bridge missing")
    ast.parse(text, filename=str(repair)); repair.write_text(text, encoding="utf-8")

    for path in DEAD:
        if not path.is_file():
            raise RuntimeError(f"dead compatibility input missing: {path}")
        refs = _production_refs(path.stem, set(DEAD))
        if refs:
            raise RuntimeError(f"{path.name} still referenced after guard cleanup: {refs}")

    # Delete proven-dead compatibility modules and remove them from fingerprints if present.
    contract=CONTRACT.read_text(encoding="utf-8")
    for path in DEAD:
        rel=path.relative_to(ROOT).as_posix()
        contract=contract.replace(repr(rel)+", ","").replace(", "+repr(rel),"").replace(repr(rel),"")
        path.unlink()
    ast.parse(contract, filename=str(CONTRACT)); CONTRACT.write_text(contract, encoding="utf-8")

    print("final VoxCPM surgery source-owned")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
