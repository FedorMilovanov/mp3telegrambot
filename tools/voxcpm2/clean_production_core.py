#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clean production core for Dub Studio.

The bot and manual PowerShell launch share the same direct renderer and master.
No subprocess proxy or VoxCPM monkeypatch is installed. Durable checkpoints are
accepted only under a fingerprint of the actual renderer modules, selected model
snapshot and backend runtime. A baseline becomes release-complete only after the
final encoded AAC files pass media QA.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import professional_audio_qa_v45
from tools.voxcpm2 import professional_audio_v45
from tools.voxcpm2 import semantic_tts_guard_v4
from services.speech_backends import DEFAULT_BACKEND_ID, get_backend

POLICY = "clean-direct-production-v2"
TARGET_SECONDS = 4.2
MAX_SECONDS = 5.4
MASTER_I = -16.0
MASTER_LRA = 8.0
MASTER_TP = -1.5


def log(message: str) -> None:
    print(f"[CLEAN-DUB] {message}", flush=True)


def _finite(value: Any, *, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ğ¾Ğµ Ğ·Ğ½Ğ°Ñ‡ĞµĞ½Ğ¸Ğµ {field}: {value!r}") from exc
    if not math.isfinite(result):
        raise RuntimeError(f"{field} Ğ´Ğ¾Ğ»Ğ¶ĞµĞ½ Ğ±Ñ‹Ñ‚ÑŒ ĞºĞ¾Ğ½ĞµÑ‡Ğ½Ñ‹Ğ¼ Ñ‡Ğ¸ÑĞ»Ğ¾Ğ¼.")
    return result


def group_source_cues(cues: list[Any]) -> list[dict[str, Any]]:
    groups = dub_quality_v4.group_cues_v4(
        cues,
        target_seconds=TARGET_SECONDS,
        max_seconds=MAX_SECONDS,
    )
    _validate_groups(groups, "english")
    return groups


def group_ready_srt(cues: list[Any]) -> list[dict[str, Any]]:
    groups = dub_quality_v4.group_ready_srt_v4(cues, max_seconds=MAX_SECONDS)
    _validate_groups(groups, "source")
    return groups


def _validate_groups(groups: list[dict[str, Any]], text_key: str) -> None:
    if not groups:
        raise RuntimeError("ĞŸĞ¾ÑĞ»Ğµ ÑĞµĞ³Ğ¼ĞµĞ½Ñ‚Ğ°Ñ†Ğ¸Ğ¸ Ğ½Ğµ Ğ¾ÑÑ‚Ğ°Ğ»Ğ¾ÑÑŒ Ñ€ĞµÑ‡ĞµĞ²Ñ‹Ñ… Ğ±Ğ»Ğ¾ĞºĞ¾Ğ².")
    previous_end = 0.0
    for index, item in enumerate(groups, start=1):
        start = _finite(item.get("start"), field=f"group[{index}].start")
        end = _finite(item.get("end"), field=f"group[{index}].end")
        text = re.sub(r"\s+", " ", str(item.get(text_key) or "")).strip()
        if start < 0.0 or not text or end <= start:
            raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ñ‹Ğ¹ Ñ€ĞµÑ‡ĞµĞ²Ğ¾Ğ¹ Ğ±Ğ»Ğ¾Ğº #{index}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Ğ ĞµÑ‡ĞµĞ²Ñ‹Ğµ Ğ±Ğ»Ğ¾ĞºĞ¸ Ğ¿ĞµÑ€ĞµÑĞµĞºĞ°ÑÑ‚ÑÑ Ğ¾ĞºĞ¾Ğ»Ğ¾ #{index}.")
        if end - start > MAX_SECONDS + 0.30:
            raise RuntimeError(
                f"Ğ ĞµÑ‡ĞµĞ²Ğ¾Ğ¹ Ğ±Ğ»Ğ¾Ğº #{index} ÑĞ»Ğ¸ÑˆĞºĞ¾Ğ¼ Ğ´Ğ»Ğ¸Ğ½Ğ½Ñ‹Ğ¹: {end - start:.3f} ÑĞµĞº."
            )
        previous_end = end


def build_render_segments(
    groups: list[dict[str, Any]],
    translations: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    segments, subtitles = professional_audio_v45.build_render_segments_v45(
        groups,
        translations,
        delay_ms=delay_ms,
        duration=duration,
    )
    _mark_and_validate_segments(segments, duration)
    return segments, subtitles


def build_direct_segments(
    groups: list[dict[str, Any]],
    *,
    delay_ms: int,
    duration: float,
) -> tuple[list[dict[str, Any]], list[pipeline.Cue]]:
    segments, subtitles = professional_audio_v45.build_direct_segments_v45(
        groups,
        delay_ms=delay_ms,
        duration=duration,
    )
    _mark_and_validate_segments(segments, duration)
    return segments, subtitles


def _mark_and_validate_segments(
    segments: list[dict[str, Any]],
    duration: float,
) -> None:
    duration_value = _finite(duration, field="video_duration")
    if duration_value <= 0.0:
        raise RuntimeError("video_duration Ğ´Ğ¾Ğ»Ğ¶ĞµĞ½ Ğ±Ñ‹Ñ‚ÑŒ > 0.")
    if not segments:
        raise RuntimeError("Ğ¡Ğ¿Ğ¸ÑĞ¾Ğº Ñ€ĞµĞ¿Ğ»Ğ¸Ğº Ğ¿ĞµÑ€ĞµĞ´ speech backend Ğ¿ÑƒÑÑ‚.")
    previous_end = 0.0
    previous_effective_end = 0.0
    seen_ids: set[int] = set()
    for item in segments:
        item["production_policy"] = POLICY
        segment_id = int(item["id"])
        if segment_id <= 0 or segment_id in seen_ids:
            raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ñ‹Ğ¹ Ğ¸Ğ»Ğ¸ Ğ¿Ğ¾Ğ²Ñ‚Ğ¾Ñ€Ğ½Ñ‹Ğ¹ ID Ñ€ĞµĞ¿Ğ»Ğ¸ĞºĞ¸: {segment_id}.")
        seen_ids.add(segment_id)
        start = _finite(item.get("start"), field=f"segment[{segment_id}].start")
        end = _finite(item.get("end"), field=f"segment[{segment_id}].end")
        try:
            delay_ms = int(item.get("start_delay_ms", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ñ‹Ğ¹ delay Ñ€ĞµĞ¿Ğ»Ğ¸ĞºĞ¸ #{segment_id}.") from exc
        if not 0 <= delay_ms <= 1500:
            raise RuntimeError(f"Delay Ñ€ĞµĞ¿Ğ»Ğ¸ĞºĞ¸ #{segment_id} Ğ²Ğ½Ğµ Ğ´Ğ¸Ğ°Ğ¿Ğ°Ğ·Ğ¾Ğ½Ğ° 0..1500 ms.")
        delay = delay_ms / 1000.0
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if start < 0.0 or not text or end <= start:
            raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ğ°Ñ Ñ€ĞµĞ¿Ğ»Ğ¸ĞºĞ° #{segment_id}.")
        if start < previous_end - 0.001:
            raise RuntimeError(f"Ğ ĞµĞ¿Ğ»Ğ¸ĞºĞ° #{segment_id} Ğ¿ĞµÑ€ĞµÑĞµĞºĞ°ĞµÑ‚ÑÑ Ñ Ğ¿Ñ€ĞµĞ´Ñ‹Ğ´ÑƒÑ‰ĞµĞ¹.")
        effective_start = start + delay
        effective_end = end + delay
        if effective_start < previous_effective_end - 0.001:
            raise RuntimeError(
                f"Ğ ĞµĞ¿Ğ»Ğ¸ĞºĞ° #{segment_id} Ğ¿ĞµÑ€ĞµÑĞµĞºĞ°ĞµÑ‚ÑÑ Ğ¿Ğ¾ÑĞ»Ğµ Ğ¿Ñ€Ğ¸Ğ¼ĞµĞ½ĞµĞ½Ğ¸Ñ delay."
            )
        if effective_end > duration_value + 0.02:
            raise RuntimeError(f"Ğ ĞµĞ¿Ğ»Ğ¸ĞºĞ° #{segment_id} Ğ²Ñ‹Ñ…Ğ¾Ğ´Ğ¸Ñ‚ Ğ·Ğ° ĞºĞ¾Ğ½ĞµÑ† Ğ²Ğ¸Ğ´ĞµĞ¾.")
        if end - start > MAX_SECONDS + 0.30:
            raise RuntimeError(
                f"Ğ ĞµĞ¿Ğ»Ğ¸ĞºĞ° #{segment_id} ÑĞ»Ğ¸ÑˆĞºĞ¾Ğ¼ Ğ´Ğ»Ğ¸Ğ½Ğ½Ğ°Ñ: {end - start:.3f} ÑĞµĞº."
            )
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        rate = words / max(0.35, end - start)
        if rate > 6.2:
            raise RuntimeError(
                f"Ğ ĞµĞ¿Ğ»Ğ¸ĞºĞ° #{segment_id} Ñ„Ğ¸Ğ·Ğ¸Ñ‡ĞµÑĞºĞ¸ Ğ¿ĞµÑ€ĞµĞ³Ñ€ÑƒĞ¶ĞµĞ½Ğ°: {rate:.2f} ÑĞ»Ğ¾Ğ²Ğ°/Ñ."
            )
        previous_end = end
        previous_effective_end = effective_end


def build_calm_references(
    *,
    source: Path,
    cues: list[pipeline.Cue],
    duration: float,
    reference_dir: Path,
) -> tuple[Path, Path]:
    """Legacy public helper; production entrypoints use continuous-reference v2."""
    reference_dir.mkdir(parents=True, exist_ok=True)
    extended = reference_dir / "extended_reference.wav"
    composite = reference_dir / "composite_reference.wav"
    extended_intervals, composite_intervals = pipeline.reference_intervals(cues, duration)
    professional_audio_v45.build_reference_v45(
        source,
        extended_intervals,
        extended,
        target_seconds=9.0,
    )
    professional_audio_v45.build_reference_v45(
        source,
        composite_intervals,
        composite,
        target_seconds=8.0,
    )
    _validate_reference_report(extended.with_suffix(".selection.json"), "extented")
    _validate_reference_report(composite.with_suffix(".selection.json"), "composite")
    return extended, composite


def _validate_reference_report(path: Path, label: str) -> None:
    if not path.is_file():
        raise RuntimeError(f"ĞĞµÑ‚ Ñ‚Ñ€ĞµĞ¹ÑĞ°Ğ±Ğ¸Ğ»Ğ¸Ñ‚Ñ‹ ÑĞµĞ»ĞµĞºÑ†Ğ¸Ğ¸ reference {label}.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ñ‹Ğ¹ reference report: {path.name}.") from exc
    if not isinstance(payload, dict) or not payload.get("passed"):
        raise RuntimeError(f"Reference {label} Ğ½Ğµ Ğ¿Ñ€Ğ¾ÑˆÑ‘Ğ» Ñ€ĞµĞ³Ñ€ĞµÑÑĞ¸ÑĞ½ Ñ€ĞµĞ¿Ğ¾Ñ€Ñ‚.")


def _load_markers(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_markers(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f"{path.suffix}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _payload_digest(value: Any) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fingerprints_match(existing: Any, current: dict[str, Any]) -> bool:
    """Accept only checkpoints built by the current render/model/runtime."""
    if not isinstance(existing, dict):
        return False
    for key in ("contract_schema", "render_contract_sha256", "release_contract_sha256"):
        if existing.get(key) != current.get(key):
            return False
    return existing.get("model_manifest") == current.get("model_manifest") and existing.get("runtime_manifest") == current.get("runtime_manifest")


def _markers_cover_checkpoints(
    markers: dict[str, Any],
    fingerprints: dict[str, Any],
    *,
    require_release: bool,
) -> bool:
    if not _fingerprints_match(markers, fingerprints):
        return False
    if not markers.get("tts_complete"):
        return False
    if not markers.get("master_complete"):
        return False
    if require_release and not markers.get("release_complete"):
        return False
    return True


def _segments_digest(segments: list[dict[str, Any]]) -> str:
    return _payload_digest(
        [
            {
                "id": int(item["id"]),
                "start": round(float(item["start"]), 6)),
                "end": round(float(item["end"]), 6)),
                "start_delay_ms": int(item.get("start_delay_ms", 0)),
                "text": str(item["text"]),
            }
            for item in segments
        ]
    )


def _bool_flag(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"ĞĞµĞºĞ¾Ñ€Ñ€ĞµĞºÑ‚Ğ½Ñ‹Ğ¹ Ğ±ÑƒĞ»ĞµĞ² Ğ¿Ğ°Ğ¼ĞµÑ‚Ñ€: Ğ¿Ğ¾Ğ»ÑƒÑ‡ĞµĞ½Ğ¾ {value!r}")


def render_and_master(
    *
    root: Path,
    source_video: Path,
    reference_dir: Path,
    segments: list[dict[str, Any]],
    subtitles: list[pipeline.Cue],
    duration: float,
    vox_archive: Path,
    cpu_python: Path,
    original_level: float,
    threads: int,
    steps: int,
    cfg: float,
    base_seed: int,
    mixed_video: Path,
    russian_only_video: Path,
    force_fresh: bool = False,
    speech_backend: str = DEFAULT_BACKEND_ID,
    speech_options: dict[str, Any] | None = None,
    backend_config: dict[str, Any] | None = None,
    media_master: Any = None,
    final_validator: Any = None,
) -> dict[str, Any]:
    root = Path(root)
    source_video = Path(source_video)
    reference_dir = Path(reference_dir)
    vox_archive = Path(vox_archive)
    cpu_python = Path(cpu_python
    mixed_video = Path(mixed_video)
    russian_only_video = Path(russian_only_video)

    _mark_and_validate_segments(segments, duration)
    root = root.resolve()
    repo_root = Path(__file__).resolve().parents[2]
    backend = get_backend(speech_backend)
    backend_request = {
        "speech_backend": backend.backend_id,
        "speech_options": dict(speech_options or {}),
        "backend_config": dict(backend_config or {}),
    }
    runtime = backend.runtime_paths(repo_root, backend_request, fallback_python=cpu_python)
    model_root = backend.model_root(backend_request, runtime, vox_archive)
    preflight_request = {
        *BackendPreflightRequest(
            repo-root=repo_root,
            runtime=runtime,
            model_root=model_root,
            base_seed=base_seed,
            speech_options=dict(speech_options or {}),
            backend_config=dict(backend_config or {}),
        ).as_dict()
    }
    backend_preflight = backend.preflight(preflight_request)
    master_policy = media_master or _ConstantMixMaster()[²È="26%öf–ÇW&R†FWF–Â“ ¢""$66WBöæÇ’f–ÇW&W2v†÷6RVÆ—G’6öFRÇ&VG’–çfÆ–FFVB6†V6·ö–çBâ"" ¢æ÷&ÖÆ—¦VBÒ7G"†FWF–Â÷"""’æ66VföÆB‚’ç&WÆ6R‚-"Â-R"¢–bæ÷Bæ÷&ÖÆ—¦VC ¢&WGW&âfÇ6P¢–bç’†Ö&¶W"–âæ÷&ÖÆ—¦VBf÷"Ö&¶W"–âôäôåõ$UE%”$ÄUô”äe$5E%T5EU$UôÔ$´U%2“ ¢&WGW&âfÇ6P¢&WGW&âç’†Ö&¶W"–âæ÷&ÖÆ—¦VBf÷"Ö&¶W"–âõ$UE%”$ÄUôDTÄ•dU%•ôÔ$´U%2 ¦FVböF—&V7Eöf–ÇW&U÷&W÷'B‡&ö÷C¢ç’’Óâ7G# ¢G'“ ¢F‚ÒF‚‡&ö÷B’ç&W6öÇfR‚’ò'6VvÖVçE÷v÷&²"ò&F—&V7E÷&VæFW&W%öf–ÇW&Ræ§6öâ ¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"Âõ4W'&÷"“ ¢&WGW&â" ¢–bæ÷BF‚æ—5öf–ÆR‚“ ¢&WGW&â" ¢G'“ ¢–ÆöBÒ§6öâæÆöG2‡F‚ç&VE÷FW‡B†Væ6öF–æsÒ'WFbÓ‚×6–r"’¢W†6WB„õ4W'&÷"Â§6öâä¥4ôäFV6öFTW'&÷"“ ¢&WGW&â" ¢–bæ÷B—6–ç7Fæ6R‡–ÆöBÂF–7B“ ¢&WGW&â" ¢ÖW76vRÒ7G"‡–ÆöBævWB‚&ÖW76vR"’÷"""’ç7G&—‚¢W'&÷%÷G—RÒ7G"‡–ÆöBævWB‚&W'&÷%÷G—R"’÷"%'VçF–ÖTW'&÷""’ç7G&—‚¢&WGW&âb'¶W'&÷%÷G—WÓ¢¶ÖW76vWÒ"–bÖW76vRVÇ6R"  ¦FVböFVÆ—fW'•öf–ÇW&UöFWF–Â€¢W†3¢'VçF–ÖTW'&÷"À¢&w3¢GWÆU´ç’ÂââåÒÀ¢·v&w3¢F–7E·7G"Âç•ÒÀ¢’Óâ7G# ¢""%&V6÷fW"F†RFVWW7B6†–ÆB6W6Rv—F†÷WBG&VF–ærâöÆB&W÷'B27W'&VçBâ"" ¢W†6WF–öåöFWF–ÂÒ7G"†W†2’ç7G&—‚¢FWF–Ç3¢Æ—7E·7G%ÒÒµĞ¢6†–ÆEöFWF–ÂÒ7G"…ôÄ5Eô4„”ÄEõ5DDU%"÷"""’ç7G&—‚¢–b6†–ÆEöFWF–Ã ¢FWF–Ç2æVæB†6†–ÆEöFWF–Â ¢2F†R&VæFW&W"FVÆ–&W&FVÇ’7G&V×2Æöw2–ç7FVBöb'VffW&–ærÖç’Ö–çWFW0¢2öb÷WGWBâ—G2g&W6‚f–ÇW&R¥4ôâ—2WF†÷&—FF—fRöæÇ’v†VâF†BW†7@¢26†–ÆB&ö6W72&WGW&æVBæöâ×¦W&ó²÷F†W'v—6RâöÆFW"&W÷'B×W7Bæ÷BGW&à¢2â–æg&7G'V7GW&RW'&÷"–çFòVÆ—G’&WG'’à¢–b-	ııÍí’f÷„5Ó"&VæFW&W"}-]½ò­íMíÂ"–âW†6WF–öåöFWF–Ã ¢&ö÷BÒ·v&w2ævWB‚'&ö÷B"¢–b&ö÷B—2æöæRæB&w3 ¢&ö÷BÒ&w5³Ğ¢&W÷'EöFWF–ÂÒöF—&V7Eöf–ÇW&U÷&W÷'B‡&ö÷B¢–b&W÷'EöFWF–Ã ¢FWF–Ç2æVæB‡&W÷'EöFWF–Â¢–bW†6WF–öåöFWF–Ã ¢FWF–Ç2æVæB†W†6WF–öåöFWF–Â ¢Væ—VS¢Æ—7E·7G%ÒÒµĞ¢f÷"fÇVR–âFWF–Ç3 ¢–bfÇVRæ÷B–âVæ—VS ¢Væ—VRæVæB‡fÇVR¢&WGW&â%Æâ"æ¦ö–â‡Væ—VR ¦FVb&VæFW%öæEöÖ7FW"‚¦&w3¢ç’Â¢¦·v&w3¢ç’’Óâç“ ¢""%&WG'’VÆ—G’ÖöæÇ’f–ÇW&W2–â×Æ6Rv†–ÆR&W6W'f–ærvööB6†V6·ö–çG2â"" ¢vÆö&ÂôÄ5Eô4„”ÄEõ5DDU%  ¢G'“ ¢&WG'•öÆ–Ö—BÒ–çB„Ô…ôUDôÔD”5ôDTÄ•dU%•õ$UE$”U2¢W†6WB…G—TW'&÷"ÂfÇVTW'&÷"Â÷fW&fÆ÷tW'&÷"’2W†3 ¢&—6R'VçF–ÖTW'&÷"‚$Ô…ôUDôÔD”5ôDTÄ•dU%•õ$UE$”U2Mí½m]Ò½-Âm]½½Ââ"’g&öÒW†0¢&WG'•öÆ–Ö—BÒÖ‚ƒÂÖ–âƒ‚Â&WG'•öÆ–Ö—B’ ¢f÷"&WG'•ö–æFW‚–â&ævR‡&WG'•öÆ–Ö—B²“ ¢ôÄ5Eô4„”ÄEõ5DDU%"Ò" ¢G'“ ¢&WGW&âöÆVv7•÷&VæFW%öæEöÖ7FW"‚¦&w2Â¢¦·v&w2¢W†6WB'VçF–ÖTW'&÷"2W†3 ¢FWF–ÂÒöFVÆ—fW'•öf–ÇW&UöFWF–Â†W†2Â&w2Â·v&w2¢–bæ÷B÷&WG'–&ÆUöFVÆ—fW'•öf–ÇW&R†FWF–Â“ ¢–bFWF–ÂæBFWF–ÂÒ7G"†W†2’ç7G&—‚“ ¢&—6R'VçF–ÖTW'&÷"†FWF–Â’g&öÒW†0¢&—6P¢–b&WG'•ö–æFW‚ãÒ&WG'•öÆ–Ö—C ¢&—6R'VçF–ÖTW'&÷"€¢-	--íÍ-}]­íR6†V6·ö–çBİ-í-İí-½]İR}]ıİâ ¢b-ıí½R·&WG'•öÆ–Ö—GÒıí--íí"â	ıí½]Mİıò-í}İòı}İ¥Æç¶FWF–ÇÒ ¢’g&öÒW†0¢Æör€¢'VÆ—G’ÖöæÇ’f–ÇW&S²í]İıâ=ı]İ½R6†V6·ö–çG2‚}ı=­â ¢b---íÍ-}]­’ıí--í·&WG'•ö–æFW‚²Ò÷·&WG'•öÆ–Ö—GÒâ ¢b-	ı}İ¢¶FWF–Å³£#×Ò ¢ ¢&—6R'VçF–ÖTW'&÷"‚-	İ]Mí-mÍíRí-íıİRWFöÖF–2FVÆ—fW'’&WG'’â" ¥õöÆÅõòÒ6÷'FVB€¢6WB†æÖRf÷"æÖR–âvÆö&Ç2‚’–bæ÷BæÖRç7F'G7v—F‚‚%õò"’æBæÖRÒ%öÆVv7’"¢Â°¢$4„”ÄEõ•D„ôåõôÄ”5’"À¢$DTÄ•dU%•õ$UE%•õôÄ”5’"À¢$Ô…ôUDôÔD”5ôDTÄ•dU%•õ$UE$”U2"À¢%ôÄ5Eô4„”ÄEõ5DDU%""À¢%ö6†–ÆE÷—F†öåöVçb"À¢%ö6öÖÖæEöfÆr"À¢%öFVÆ—fW'•öf–ÇW&UöFWF–Â"À¢%öF—&V7Eöf–ÇW&U÷&W÷'B"À¢%öf–æ—FR"À¢%ö—5öÖ7FW%ö6öÖÖæB"À¢%ö—5öÖ7FW%÷&VÆV6Uö6öÖÖæB"À¢%ö—5÷—F†öå÷67&—Eö6öÖÖæB"À¢%öÆVv7•÷&VæFW%öæEöÖ7FW""À¢%öÖ&µöæE÷fÆ–FFU÷6VvÖVçG2"À¢%÷&WG'–&ÆUöFVÆ—fW'•öf–ÇW&R"À¢%÷'Våö6†–ÆE÷&ö6W72"À¢%÷7G&–7Eö–çB"À¢%÷fW&–g•÷÷7Eö5öÖ7FW%ö÷WGWB"À¢'&VæFW%öæEöÖ7FW""À¢Ğ¢ 