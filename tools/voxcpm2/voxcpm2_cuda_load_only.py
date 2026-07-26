#!/usr/bin/env python3
"""Load VoxCPM2 on CUDA once, synchronize, unload, and write an atomic report."""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def configure_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def looks_like_model_dir(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "config.json").exists()
        and (
            (path / "model.safetensors").exists()
            or any(path.glob("*.safetensors"))
            or any(path.glob("*.bin"))
        )
    )


def newest_snapshot(path: Path) -> Path | None:
    snapshots = path / "snapshots"
    if not snapshots.is_dir():
        return None
    candidates = [item for item in snapshots.iterdir() if looks_like_model_dir(item)]
    return max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None


def discover_model(archive_root: Path, explicit: str | None) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if looks_like_model_dir(candidate):
            return candidate
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot
        raise RuntimeError(f"model path is not a VoxCPM2 snapshot: {candidate}")

    candidates = [
        archive_root
        / "models"
        / "voxcpm2-model-cache"
        / "models--openbmb--VoxCPM2",
        archive_root
        / "models"
        / "voxcpm2-model-cache"
        / "models--OpenBMB--VoxCPM2",
    ]
    for candidate in candidates:
        if looks_like_model_dir(candidate):
            return candidate
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot

    for candidate in archive_root.rglob("models--openbmb--VoxCPM2"):
        snapshot = newest_snapshot(candidate)
        if snapshot:
            return snapshot
    raise RuntimeError("local VoxCPM2 snapshot was not found")


def cuda_memory(torch: Any) -> dict[str, int]:
    free_bytes, total_bytes = torch.cuda.mem_get_info(0)
    return {
        "allocated": int(torch.cuda.memory_allocated(0)),
        "reserved": int(torch.cuda.memory_reserved(0)),
        "max_allocated": int(torch.cuda.max_memory_allocated(0)),
        "max_reserved": int(torch.cuda.max_memory_reserved(0)),
        "free": int(free_bytes),
        "total": int(total_bytes),
    }


def main() -> int:
    configure_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--model-path")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--memory-fraction", type=float, default=0.70)
    args = parser.parse_args()

    if not 0.65 <= args.memory_fraction <= 0.85:
        raise RuntimeError("load-only memory fraction must be within 0.65..0.85")

    archive_root = args.archive_root.expanduser().resolve()
    report_path = args.report.expanduser().resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "test": "voxcpm2_cuda_load_only",
        "status": "starting",
        "started_unix": time.time(),
        "archive_root": str(archive_root),
        "memory_fraction_limit": args.memory_fraction,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "cuda_launch_blocking": os.environ.get("CUDA_LAUNCH_BLOCKING"),
        "cuda_device_max_connections": os.environ.get("CUDA_DEVICE_MAX_CONNECTIONS"),
        "pytorch_no_cuda_memory_caching": os.environ.get(
            "PYTORCH_NO_CUDA_MEMORY_CACHING"
        ),
    }
    write_report(report_path, report)

    model: Any = None
    torch: Any = None
    try:
        if not archive_root.is_dir():
            raise RuntimeError(f"archive root does not exist: {archive_root}")

        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        import torch as imported_torch
        from voxcpm import VoxCPM

        torch = imported_torch
        report["torch_version"] = torch.__version__
        report["torch_cuda_build"] = torch.version.cuda
        report["cuda_available"] = bool(torch.cuda.is_available())
        if not torch.version.cuda:
            raise RuntimeError("selected PyTorch build has no CUDA runtime")
        if not report["cuda_available"]:
            raise RuntimeError("torch.cuda.is_available() returned False")

        torch.cuda.init()
        torch.cuda.set_device(0)
        torch.cuda.synchronize(0)
        set_fraction = getattr(torch.cuda, "set_per_process_memory_fraction", None)
        if callable(set_fraction):
            set_fraction(args.memory_fraction, device=0)

        properties = torch.cuda.get_device_properties(0)
        report["device"] = {
            "index": 0,
            "name": properties.name,
            "compute_capability": [properties.major, properties.minor],
            "multiprocessor_count": int(properties.multi_processor_count),
        }
        report["memory_before_load_bytes"] = cuda_memory(torch)

        model_path = discover_model(archive_root, args.model_path)
        report["model_path"] = str(model_path)
        report["status"] = "loading_model"
        write_report(report_path, report)
        print(f"model: {model_path}", flush=True)
        print(
            f"CUDA: {torch.__version__} / runtime {torch.version.cuda} / "
            f"{properties.name}",
            flush=True,
        )
        print(f"memory fraction limit: {args.memory_fraction:.2f}", flush=True)

        load_started = time.perf_counter()
        model = VoxCPM.from_pretrained(
            str(model_path),
            device="cuda",
            optimize=False,
            load_denoiser=False,
        )
        torch.cuda.synchronize(0)
        report["model_load_seconds"] = round(time.perf_counter() - load_started, 3)
        report["memory_after_load_bytes"] = cuda_memory(torch)
        report["status"] = "loaded"
        write_report(report_path, report)
        print("MODEL LOADED AND CUDA SYNCHRONIZED", flush=True)

        del model
        model = None
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize(0)
        report["memory_after_unload_bytes"] = cuda_memory(torch)
        report["status"] = "passed"
        report["finished_unix"] = time.time()
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 0
    except BaseException as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()[-12000:]
        report["finished_unix"] = time.time()
        if torch is not None and getattr(torch, "cuda", None) is not None:
            try:
                report["memory_at_failure_bytes"] = cuda_memory(torch)
            except BaseException:
                pass
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        try:
            del model
        except BaseException:
            pass
        gc.collect()
        if torch is not None:
            try:
                torch.cuda.empty_cache()
            except BaseException:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
