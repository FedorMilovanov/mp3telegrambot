"""Backend-neutral orchestration of speech synthesis, media mastering and QA."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from services.dub_reference_strategies import reference_strategy_for_backend
from services.media_masters import (
    MediaMasterRequest,
    get_final_validator,
    get_media_master,
)
from services.speech_backends import DEFAULT_BACKEND_ID, get_backend

DUB_RENDERING_POLICY = "separated-speech-master-validator-orchestration-v1"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run(command: list[str], *, cwd: Path, env: dict[str, str], label: str) -> None:
    result = subprocess.run(command, cwd=str(cwd), env=env, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{label} завершился с кодом {result.returncode}.")


def run_speech_master_validation(
    *,
    root: Path,
    request: dict[str, Any],
    source: Path,
    cues: list[Any],
    duration: float,
    segments_json: Path,
    final_mixed: Path,
    final_russian: Path,
    require_production_capabilities: bool = True,
) -> Path:
    """Run three explicit components without assigning media ownership to TTS."""
    root = Path(root).resolve()
    repo = Path(__file__).resolve().parent.parent
    backend = get_backend(request.get("speech_backend") or DEFAULT_BACKEND_ID)
    capabilities = backend.capabilities()
    missing = capabilities.missing()
    if require_production_capabilities and missing:
        raise RuntimeError(
            f"Speech backend {backend.backend_id} lacks production capabilities: "
            f"{', '.join(missing)}."
        )

    reference_dir = root / "references"
    audio_dir = root / "audio"
    segment_work = root / "segment_work"
    master_work = root / "master_work"
    output_dir = root / "output"
    for directory in (
        reference_dir,
        audio_dir,
        segment_work,
        master_work,
        output_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    references = reference_strategy_for_backend(backend.backend_id).prepare(
        source_video=Path(source),
        cues=list(cues),
        duration=float(duration),
        reference_dir=reference_dir,
    )
    runtime = backend.runtime_paths(repo, request)
    if not runtime.cpu_python.is_file():
        raise RuntimeError(
            f"CPU Python не найден для backend={backend.backend_id}: "
            f"{runtime.cpu_python}"
        )

    threads = max(1, int(request.get("threads") or 10))
    steps = max(1, int(request.get("steps") or 16))
    cfg = float(request.get("cfg") or 1.8)
    video_id = str(request["video_id"])
    russian_timeline = audio_dir / f"{video_id}_ru_timeline.wav"
    execution_plan_log = output_dir / "backend_generation_execution_plans.jsonl"
    execution_plan_log.unlink(missing_ok=True)
    env = backend.process_environment(
        {"threads": threads, "speech_backend": backend.backend_id},
        base_environment=os.environ,
    ).as_dict(os.environ)
    env["DUB_BACKEND_EXECUTION_PLAN_LOG"] = str(execution_plan_log)

    synth = backend.build_renderer_command(
        runtime,
        values={
            "extended_reference": str(references.extended_reference or ""),
            "composite_reference": str(references.composite_reference or ""),
            "segments_json": str(segments_json),
            "segment_work": str(segment_work),
            "timeline": str(russian_timeline),
            "threads": str(threads),
            "steps": str(steps),
            "cfg": str(cfg),
            "cache_length": str(int(request.get("cache_length") or 4096)),
            "duration": f"{duration:.6f}",
            "base_seed": str(int(request.get("base_seed") or 2026072800)),
        },
    )
    _run(
        synth,
        cwd=repo,
        env=env,
        label=f"Speech backend {backend.backend_id}",
    )
    if backend.backend_id == "voxcpm2" and not execution_plan_log.is_file():
        raise RuntimeError(
            "VoxCPM2 завершил synthesis без exact execution-plan evidence."
        )

    master = get_media_master(request.get("media_master") or "constant-mix")
    master_runtime = master.runtime_paths(
        repo,
        request,
        fallback_python=runtime.cpu_python,
    )
    master_request = MediaMasterRequest(
        source_video=Path(source),
        russian_wav=russian_timeline,
        work_dir=master_work,
        mixed_video=Path(final_mixed),
        russian_only_video=Path(final_russian),
        original_level=float(request.get("original_level") or 0.18),
        target_i=float(request.get("target_i") or -14.0),
        target_lra=float(request.get("target_lra") or 9.0),
        target_tp=float(request.get("target_tp") or -1.0),
    )
    master_command = master.build_command(master_runtime, master_request)
    _run(
        master_command,
        cwd=repo,
        env=env,
        label=f"Media master {master.master_id}",
    )

    validator = get_final_validator(
        request.get("final_media_validator") or "ffprobe-av-contract"
    )
    validation = validator.validate(
        mixed_video=Path(final_mixed),
        russian_only_video=Path(final_russian),
    )
    report = {
        "schema_version": 1,
        "policy": DUB_RENDERING_POLICY,
        "speech_backend": backend.identity(runtime.archive_root).as_dict(),
        "capabilities": capabilities.as_dict(),
        "references": references.as_dict(),
        "media_master": master_runtime.as_dict(),
        "final_validation": validation.as_dict(),
        "speech_command": synth,
        "master_command": master_command,
        "execution_plan_log": (
            str(execution_plan_log) if execution_plan_log.is_file() else ""
        ),
        "timeline": str(russian_timeline),
    }
    _atomic_json(output_dir / "render_architecture.json", report)
    return russian_timeline


__all__ = ["DUB_RENDERING_POLICY", "run_speech_master_validation"]
