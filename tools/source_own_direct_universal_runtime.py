#!/usr/bin/env python3
"""Move direct universal runtime wrappers into the canonical CLI source owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
UNIVERSAL = ROOT / "tools" / "voxcpm2" / "direct_universal_runtime.py"

BLOCK = r'''
UNIVERSAL_RUNTIME_POLICY = universal_runtime.POLICY
_UNIVERSAL_STATE: dict[str, Any] = {
    "segments": {},
    "work_dir": None,
    "current_segment_id": None,
    "total_segments": 0,
}
_universal_base_read_segments = read_segments
_universal_base_load_retry_epoch = load_retry_epoch
_universal_base_invalidate_segment_for_retry = invalidate_segment_for_retry
_universal_base_seed_for_attempt = seed_for_attempt
_universal_base_acceptable_candidates = _acceptable_candidates
_universal_base_raw_failure_evidence = _raw_failure_evidence


def read_segments(path: Path) -> list[dict[str, Any]]:
    segments = list(_universal_base_read_segments(Path(path)))
    _UNIVERSAL_STATE["segments"] = universal_runtime._segments_by_id(segments)
    _UNIVERSAL_STATE["total_segments"] = len(segments)
    return segments


def _universal_segment(segment_id: Any) -> dict[str, Any] | None:
    try:
        return _UNIVERSAL_STATE["segments"].get(int(segment_id))
    except (TypeError, ValueError, OverflowError):
        return None


def _universal_scope(
    work_dir: Path,
    segment_id: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any], str]:
    segment = _universal_segment(segment_id)
    context = direct_timing_guard.load_signature_context(work_dir)
    if not isinstance(segment, dict):
        return None, context, ""
    profile = str(segment.get("reference_profile") or "extended")
    reference = work_dir.resolve() / "references_guarded" / f"{profile}.wav"
    if reference.is_file():
        context = {
            **context,
            "reference_profile": profile,
            "reference_sha256": str(sha256_file(reference)),
        }
    fingerprint = direct_timing_guard.failure_scope_fingerprint(
        segment,
        signature_context=context,
    )
    return segment, context, fingerprint


def load_retry_epoch(work_dir: Path, segment_id: Any) -> int:
    _UNIVERSAL_STATE["work_dir"] = Path(work_dir).resolve()
    segment, _context, scope = _universal_scope(Path(work_dir), segment_id)
    if isinstance(segment, dict) and scope:
        try:
            return int(
                _universal_base_load_retry_epoch(
                    work_dir,
                    segment_id,
                    scope_fingerprint=scope,
                )
            )
        except TypeError:
            pass
    return int(_universal_base_load_retry_epoch(work_dir, segment_id))


def invalidate_segment_for_retry(
    work_dir: Path,
    segment: dict[str, Any],
    *,
    reason: str,
    fitted_path: Path | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _segment_value, context, scope = _universal_scope(
        Path(work_dir),
        segment.get("id"),
    )
    enriched = dict(evidence or {})
    enriched["failure_scope_fingerprint"] = scope or (
        direct_timing_guard.failure_scope_fingerprint(
            segment,
            signature_context=context,
        )
    )
    return _universal_base_invalidate_segment_for_retry(
        work_dir,
        segment,
        reason=reason,
        fitted_path=fitted_path,
        evidence=enriched,
    )


def seed_for_attempt(
    base_seed: int,
    segment_id: int,
    attempt: int,
    retry_epoch: int,
) -> int:
    _UNIVERSAL_STATE["current_segment_id"] = int(segment_id)
    total = max(1, int(_UNIVERSAL_STATE.get("total_segments") or 1))
    ordered = sorted(int(value) for value in _UNIVERSAL_STATE["segments"])
    try:
        position = ordered.index(int(segment_id)) + 1
    except ValueError:
        position = min(max(1, int(segment_id)), total)
    segment = _universal_segment(segment_id) or {"text": ""}
    slot = float(segment.get("speech_slot") or 1.0)
    work_dir = _UNIVERSAL_STATE.get("work_dir")
    context = (
        _universal_scope(Path(work_dir), segment_id)[1]
        if work_dir is not None
        else {}
    )
    if int(attempt) == 1 and work_dir is not None:
        direct_timing_guard.enforce_retry_epoch_budget(
            work_dir=Path(work_dir),
            segment=segment,
            retry_epoch=int(retry_epoch),
            signature_context=context,
        )
    plan = direct_timing_guard.candidate_efficiency_plan(
        segment,
        speech_slot=max(0.001, slot),
        retry_epoch=int(retry_epoch),
        max_tempo=MAX_TEMPO,
    )
    max_attempts = int(plan.get("max_attempts") or 5)
    log(
        "DUB_PROGRESS "
        + json.dumps(
            {
                "progress": universal_runtime._progress_value(
                    position=position,
                    total=total,
                    attempt=int(attempt),
                    max_attempts=max_attempts,
                ),
                "stage": (
                    f"voxcpm2 · сегмент {position}/{total} · "
                    f"вариант {int(attempt)}/{max_attempts} · "
                    f"epoch {int(retry_epoch)}"
                ),
                "policy": universal_runtime._PROGRESS_POLICY,
                "risk_band": plan.get("risk_band"),
            },
            ensure_ascii=False,
        )
    )
    return int(
        _universal_base_seed_for_attempt(
            base_seed,
            segment_id,
            attempt,
            retry_epoch,
        )
    )


def _universal_current_segment() -> dict[str, Any] | None:
    return _universal_segment(_UNIVERSAL_STATE.get("current_segment_id"))


def _acceptable_candidates(
    candidates: list[dict[str, Any]],
    speech_slot: float,
) -> list[dict[str, Any]]:
    acceptable = list(_universal_base_acceptable_candidates(candidates, speech_slot))
    segment = _universal_current_segment()
    if not isinstance(segment, dict) or acceptable:
        return acceptable
    retry_epoch = int(segment.get("retry_epoch") or 0)
    work_dir = _UNIVERSAL_STATE.get("work_dir")
    context = (
        _universal_scope(Path(work_dir), segment.get("id"))[1]
        if work_dir is not None
        else {}
    )
    timing_failure = direct_timing_guard.evaluate_dynamic_timing_failure(
        candidates,
        segment=segment,
        speech_slot=float(speech_slot),
        retry_epoch=retry_epoch,
        max_tempo=MAX_TEMPO,
    )
    if timing_failure is not None and work_dir is not None:
        block = direct_timing_guard.persist_timing_block(
            Path(work_dir),
            segment=segment,
            signature_context=context,
            retry_epoch=retry_epoch,
            evidence=timing_failure,
        )
        raise RuntimeError(
            direct_timing_guard.format_timing_block_message(block, repeated=False)
        )
    plan = direct_timing_guard.candidate_efficiency_plan(
        segment,
        speech_slot=float(speech_slot),
        retry_epoch=retry_epoch,
        max_tempo=MAX_TEMPO,
    )
    budget = int(plan.get("max_attempts") or 5)
    if len(candidates) >= budget:
        summary = ", ".join(
            f"#{int(item.get('attempt') or 0)}: "
            f"score={float(item.get('score') or 0.0):.1f}, "
            f"tempo={float(item.get('required_tempo') or 0.0):.3f}"
            for item in candidates
        )
        raise RuntimeError(
            f"Сегмент #{int(segment.get('id') or 0)}: адаптивный бюджет "
            f"{budget} кандидатов исчерпан (risk={plan.get('risk_band')}); "
            f"hard-quality кандидат не найден. {summary}"
        )
    return acceptable


def _raw_failure_evidence(
    candidates: list[dict[str, Any]],
    *,
    speech_slot: float,
    retry_epoch: int,
) -> dict[str, Any]:
    payload = dict(
        _universal_base_raw_failure_evidence(
            candidates,
            speech_slot=speech_slot,
            retry_epoch=retry_epoch,
        )
    )
    segment = _universal_current_segment()
    work_dir = _UNIVERSAL_STATE.get("work_dir")
    if isinstance(segment, dict):
        context = (
            _universal_scope(Path(work_dir), segment.get("id"))[1]
            if work_dir is not None
            else {}
        )
        payload["failure_scope_fingerprint"] = (
            direct_timing_guard.failure_scope_fingerprint(
                segment,
                signature_context=context,
            )
        )
    payload["universal_runtime_policy"] = universal_runtime.POLICY
    return payload
'''


def remove_top_function(text: str, name: str, path: Path) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start : (node.end_lineno or node.lineno)]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


def rewrite_all(text: str, path: Path, remove: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if not isinstance(value, list):
                raise RuntimeError("direct_universal_runtime.__all__ is not a literal list")
            exports = [item for item in value if item != remove]
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"__all__ = {exports!r}\n"]
            return "".join(lines)
    raise RuntimeError("direct_universal_runtime.__all__ not found")


def main() -> int:
    cli = CLI.read_text(encoding="utf-8")
    import_line = "from tools.voxcpm2.direct_universal_runtime import install_direct_runtime\n"
    if import_line not in cli:
        raise RuntimeError("direct CLI universal installer import not found")
    cli = cli.replace(
        import_line,
        "from tools.voxcpm2 import direct_universal_runtime as universal_runtime\n",
        1,
    )
    call = "install_direct_runtime(globals())\n"
    if call not in cli:
        raise RuntimeError("direct CLI universal installer call not found")
    cli = cli.replace(call, BLOCK.strip() + "\n\n", 1)
    if "install_direct_runtime" in cli or "namespace[" in BLOCK:
        raise RuntimeError("direct CLI universal mutation survived")
    ast.parse(cli, filename=str(CLI))
    CLI.write_text(cli, encoding="utf-8")

    universal = UNIVERSAL.read_text(encoding="utf-8")
    universal = remove_top_function(universal, "install_direct_runtime", UNIVERSAL)
    universal = rewrite_all(universal, UNIVERSAL, "install_direct_runtime")
    universal = universal.replace("from typing import Any, Callable\n", "from typing import Any\n")
    universal = universal.replace(
        '"""Universal VoxCPM2 production hardening installed by compatibility wrappers.\n\n'
        'This module contains only project-wide behavior. It does not know any video ID,\n'
        'caption text, speaker, or one-off SRT. The wrappers keep the previously audited\n'
        'implementation available as a base snapshot and apply these invariants on every\n'
        'ready-SRT direct dubbing job.\n"""',
        '"""Pure universal helpers and generic timing preflight for VoxCPM2 production.\n\n'
        'Direct CLI state and candidate/retry wrappers are owned by the canonical CLI.\n'
        'This module exposes shared calculations plus the still-separate generic preflight.\n"""',
        1,
    )
    if "def install_direct_runtime" in universal:
        raise RuntimeError("direct universal installer survived")
    ast.parse(universal, filename=str(UNIVERSAL))
    UNIVERSAL.write_text(universal, encoding="utf-8")

    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in (
            "source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_"
        )):
            continue
        if "install_direct_runtime" in path.read_text(encoding="utf-8", errors="replace"):
            blockers.append(rel)
    if blockers:
        raise RuntimeError("install_direct_runtime still referenced: " + ", ".join(sorted(set(blockers))))

    print("direct universal runtime is source-owned by canonical CLI")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
