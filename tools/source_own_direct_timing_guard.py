#!/usr/bin/env python3
"""Move surgical timing-guard behavior into direct_timing_guard source owner."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tools" / "voxcpm2" / "direct_timing_guard.py"
OLD = ROOT / "tools" / "voxcpm2" / "direct_surgical_guard.py"
CLI = ROOT / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
SURGICAL_RUNTIME = ROOT / "tools" / "voxcpm2" / "direct_surgical_runtime.py"
GENERIC_DIRECT = ROOT / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py"
CONTRACT = ROOT / "tools" / "voxcpm2" / "clean_runtime_contract.py"


def rename_top_function(text: str, path: Path, old: str, new: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == old:
            line = lines[node.lineno - 1]
            needle = f"def {old}("
            if needle not in line:
                raise RuntimeError(f"unexpected definition line for {old}: {line!r}")
            lines[node.lineno - 1] = line.replace(needle, f"def {new}(", 1)
            return "".join(lines)
    raise RuntimeError(f"{path}: function {old} not found")


def replace_top_function(text: str, path: Path, name: str, source: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            lines[start : (node.end_lineno or node.lineno)] = ["\n\n" + source.strip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


EXTRA = r'''
SURGICAL_GUARD_POLICY = "voxcpm2-surgical-timing-polish-v1"
MARKER_SCHEMA_VERSION = 2
MAX_SCOPE_EPOCHS = 3
MAX_MARKER_BYTES = 2 * 1024 * 1024
MAX_ARCHIVED_MARKERS = 8


class RetryableSynthesisFailure(RuntimeError):
    """Early stop carrying explicit retry-state semantics."""

    def __init__(
        self,
        message: str,
        *,
        segment: Mapping[str, Any],
        evidence: Mapping[str, Any] | None,
        advance_retry: bool,
        failure_kind: str,
    ) -> None:
        super().__init__(str(message))
        self.segment = dict(segment)
        self.segment_id = int(self.segment.get("id") or 0)
        self.evidence = dict(evidence or {})
        self.advance_retry = bool(advance_retry)
        self.failure_kind = str(failure_kind or "synthesis_failure")


def _archive_timing_marker(path: Path, reason: str) -> None:
    suffix = re.sub(r"[^a-z0-9_-]+", "-", reason.casefold()).strip("-")
    destination = path.with_suffix(
        path.suffix + f".stale-{suffix or 'unknown'}-{uuid.uuid4().hex[:8]}"
    )
    try:
        path.replace(destination)
    except OSError:
        path.unlink(missing_ok=True)
    archived = sorted(
        path.parent.glob(path.name + ".stale-*"),
        key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
        reverse=True,
    )
    for stale in archived[MAX_ARCHIVED_MARKERS:]:
        stale.unlink(missing_ok=True)


def _validate_segments(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [dict(item) for item in values]
    if not result:
        raise RuntimeError("Timing preflight получил пустой список сегментов.")
    seen: set[int] = set()
    previous_end = -1.0
    for position, segment in enumerate(result, 1):
        segment_id = int(segment.get("id") or position)
        start = _finite(segment.get("start"), float("nan"))
        end = _finite(segment.get("end"), float("nan"))
        tail = _finite(segment.get("tail_guard"), float("nan"))
        if segment_id <= 0 or segment_id in seen:
            raise RuntimeError(f"Некорректный или повторный ID сегмента: {segment_id}.")
        if not all(math.isfinite(value) for value in (start, end, tail)):
            raise RuntimeError(f"Сегмент #{segment_id}: тайминг должен быть конечным.")
        if start < 0.0 or end <= start or tail < 0.0 or tail >= end - start:
            raise RuntimeError(f"Сегмент #{segment_id}: некорректное речевое окно.")
        if start < previous_end - 1e-6:
            raise RuntimeError(f"Сегмент #{segment_id}: перекрытие или неправильный порядок.")
        if not _normalise(segment.get("text")):
            raise RuntimeError(f"Сегмент #{segment_id}: пустой русский текст.")
        slot = end - start - tail
        stored = segment.get("speech_slot")
        if stored is not None and abs(_finite(stored, float("nan")) - slot) > 1e-6:
            raise RuntimeError(f"Сегмент #{segment_id}: сохранённый speech_slot не совпадает.")
        segment["id"] = segment_id
        seen.add(segment_id)
        previous_end = end
    return result


def run_pre_model_guard(
    segments: Iterable[dict[str, Any]],
    *,
    work_dir: Path,
    max_tempo: float,
    signature_context: Mapping[str, Any] | None,
) -> dict[str, Any]:
    values = _validate_segments(segments)
    report = _base_run_pre_model_guard(
        values,
        work_dir=work_dir,
        max_tempo=max_tempo,
        signature_context=signature_context,
    )
    if isinstance(report, dict):
        report["surgical_guard_policy"] = SURGICAL_GUARD_POLICY
        _atomic_json(Path(work_dir).resolve() / REPORT_NAME, report)
    return report


def enforce_retry_epoch_budget(
    *,
    work_dir: Path,
    segment: Mapping[str, Any],
    retry_epoch: int,
    signature_context: Mapping[str, Any] | None,
) -> None:
    if int(retry_epoch) >= MAX_SCOPE_EPOCHS:
        raise RuntimeError(
            f"Сегмент #{int(segment.get('id') or 0)}: исчерпаны "
            f"{MAX_SCOPE_EPOCHS} seed epoch для точного входа. "
            "Измените текст, тайминг, модель, профиль или reference."
        )
    _base_enforce_retry_epoch_budget(
        work_dir=work_dir,
        segment=segment,
        retry_epoch=retry_epoch,
        signature_context=signature_context,
    )
'''

PERSIST = r'''
def persist_timing_block(
    work_dir: Path,
    *,
    segment: Mapping[str, Any],
    signature_context: Mapping[str, Any] | None,
    retry_epoch: int,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    segment_id = int(segment.get("id") or 0)
    slot = _finite(segment.get("end")) - _finite(segment.get("start")) - _finite(
        segment.get("tail_guard")
    )
    attempts = [item for item in evidence.get("attempts") or [] if isinstance(item, Mapping)]
    durations = [_finite(item.get("duration")) for item in attempts if _finite(item.get("duration")) > 0]
    max_tempo = max(0.1, _finite(evidence.get("max_tempo"), 1.36))
    best = min(durations) if durations else 0.0
    hard_slot = best / max_tempo if best else slot
    payload = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "policy": SURGICAL_GUARD_POLICY,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "segment_id": segment_id,
        "signature": failure_scope_fingerprint(
            segment, signature_context=signature_context
        ),
        "text": _normalise(segment.get("text")),
        "speech_slot": round(slot, 6),
        "retry_epoch": int(retry_epoch),
        "evidence": dict(evidence),
        "recommendation": {
            "hard_minimum_speech_slot": round(max(slot, hard_slot), 3),
            "hard_shorten_percent": int(
                math.ceil(max(0.0, 1.0 - slot / max(slot, hard_slot)) * 20.0) * 5
            ),
        },
    }
    _atomic_json(timing_block_path(work_dir, segment_id), payload)
    return payload
'''

LOAD = r'''
def load_matching_timing_block(
    work_dir: Path,
    *,
    segment: Mapping[str, Any],
    signature_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    path = timing_block_path(work_dir, int(segment.get("id") or 0))
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > MAX_MARKER_BYTES:
            _archive_timing_marker(path, "oversized")
            return None
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        _archive_timing_marker(path, "corrupt-json")
        return None
    if not isinstance(payload, dict) or (
        payload.get("schema_version") != MARKER_SCHEMA_VERSION
        or payload.get("policy") != SURGICAL_GUARD_POLICY
        or int(payload.get("segment_id") or 0) != int(segment.get("id") or 0)
        or not isinstance(payload.get("evidence"), dict)
        or not isinstance(payload.get("recommendation"), dict)
    ):
        _archive_timing_marker(path, "contract-mismatch")
        return None
    expected = failure_scope_fingerprint(
        segment, signature_context=signature_context
    )
    if payload.get("signature") == expected:
        return payload
    _archive_timing_marker(path, "input-changed")
    return None
'''

FORMAT = r'''
def format_timing_block_message(block: Mapping[str, Any], *, repeated: bool) -> str:
    evidence = block.get("evidence") if isinstance(block.get("evidence"), Mapping) else {}
    attempts = [item for item in evidence.get("attempts") or [] if isinstance(item, Mapping)]
    tempos = [_finite(item.get("required_tempo")) for item in attempts if _finite(item.get("required_tempo")) > 0]
    tempo_text = f"{min(tempos):.2f}–{max(tempos):.2f}×" if tempos else "нет данных"
    note = (
        "Повтор не запущен и новый retry epoch не расходуется."
        if repeated
        else "Оставшиеся дорогие seed остановлены."
    )
    recommendation = block.get("recommendation") or {}
    return (
        f"Сегмент #{int(block.get('segment_id') or 0)} не помещается естественно: "
        f"окно={_finite(block.get('speech_slot')):.2f} сек., required tempo={tempo_text}. "
        f"{note} Сократите текст примерно на "
        f"{int(recommendation.get('hard_shorten_percent') or 0)}% или расширьте окно."
    )
'''


def remove_release_entry(text: str, relative: str) -> str:
    tree = ast.parse(text, filename=str(CONTRACT))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "_RENDER_MODULES" for target in node.targets):
            continue
        value = ast.literal_eval(node.value)
        if relative not in value:
            raise RuntimeError(f"render fingerprint entry missing: {relative}")
        updated = tuple(item for item in value if item != relative)
        lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [f"_RENDER_MODULES = {updated!r}\n"]
        return "".join(lines)
    raise RuntimeError("_RENDER_MODULES assignment not found")


def main() -> int:
    guard = GUARD.read_text(encoding="utf-8")
    if "from datetime import datetime, timezone" not in guard:
        guard = guard.replace("from collections.abc import Iterable, Mapping\n", "from collections.abc import Iterable, Mapping\nfrom datetime import datetime, timezone\n", 1)
    guard = rename_top_function(guard, GUARD, "run_pre_model_guard", "_base_run_pre_model_guard")
    guard = rename_top_function(guard, GUARD, "enforce_retry_epoch_budget", "_base_enforce_retry_epoch_budget")
    guard = replace_top_function(guard, GUARD, "persist_timing_block", PERSIST)
    guard = replace_top_function(guard, GUARD, "format_timing_block_message", FORMAT)
    guard = guard.replace('POLICY = "voxcpm2-direct-timing-guard-v1"', 'POLICY = "voxcpm2-direct-timing-guard-v2"', 1)
    # Place public v2 wrappers after base implementations but before the final exports.
    all_index = guard.rfind("\n__all__")
    if all_index < 0:
        raise RuntimeError("direct_timing_guard __all__ not found")
    guard = guard[:all_index] + "\n\n" + EXTRA.strip() + "\n\n" + LOAD.strip() + "\n" + guard[all_index:]
    # Ensure source owner exports the new exception/policy.
    guard = guard.replace('"POLICY",', '"POLICY",\n    "SURGICAL_GUARD_POLICY",\n    "RetryableSynthesisFailure",\n    "load_matching_timing_block",', 1)
    if "guard." in guard or "install_guard_contract" in guard:
        raise RuntimeError("timing owner unexpectedly contains patch-layer references")
    ast.parse(guard, filename=str(GUARD))
    GUARD.write_text(guard, encoding="utf-8")

    cli = CLI.read_text(encoding="utf-8")
    cli = cli.replace("from tools.voxcpm2.direct_surgical_guard import install_guard_contract\n", "")
    cli = cli.replace("install_guard_contract()\n", "")
    CLI.write_text(cli, encoding="utf-8")

    runtime = SURGICAL_RUNTIME.read_text(encoding="utf-8")
    runtime = runtime.replace("from tools.voxcpm2 import direct_surgical_guard\n", "")
    runtime = runtime.replace('    "tools/voxcpm2/direct_surgical_guard.py",\n', "")
    runtime = runtime.replace("    direct_surgical_guard.install_guard_contract()\n", "")
    SURGICAL_RUNTIME.write_text(runtime, encoding="utf-8")

    generic = GENERIC_DIRECT.read_text(encoding="utf-8")
    generic = generic.replace("from tools.voxcpm2.direct_surgical_guard import install_guard_contract\n", "")
    generic = generic.replace("install_guard_contract()\n", "")
    GENERIC_DIRECT.write_text(generic, encoding="utf-8")

    contract = remove_release_entry(CONTRACT.read_text(encoding="utf-8"), "tools/voxcpm2/direct_surgical_guard.py")
    CONTRACT.write_text(contract, encoding="utf-8")

    OLD.unlink()

    # Fail if a production import/path reference to the deleted mutation layer remains.
    blockers: list[str] = []
    for path in ROOT.rglob("*.py"):
        if path.resolve() == Path(__file__).resolve() or "tests" in path.parts or ".git" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("tools/") and any(tag in rel for tag in ("source_own_", "rewrite_", "runtime_", "refactor_", "flatten_", "remove_", "prune_")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "direct_surgical_guard" in text:
            blockers.append(rel)
    if blockers:
        raise RuntimeError("deleted timing patch layer still referenced: " + ", ".join(sorted(set(blockers))))

    for path in (CLI, SURGICAL_RUNTIME, GENERIC_DIRECT, CONTRACT):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    print("direct timing guard v2 is source-owned; patch module removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
