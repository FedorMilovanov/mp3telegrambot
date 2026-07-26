#!/usr/bin/env python3
"""Run a bounded BF16 GEMM diagnostic on one CUDA device."""
from __future__ import annotations

import argparse
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


def main() -> int:
    configure_utf8()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=4)
    parser.add_argument("--memory-fraction", type=float, default=0.25)
    args = parser.parse_args()

    if not 1 <= args.repeats <= 8:
        raise RuntimeError("repeats must be within 1..8")
    if not 0.10 <= args.memory_fraction <= 0.50:
        raise RuntimeError("memory fraction must be within 0.10..0.50")

    report_path = args.report.expanduser().resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "test": "cuda_bf16_gemm_probe",
        "status": "starting",
        "started_unix": time.time(),
        "repeats": args.repeats,
        "memory_fraction_limit": args.memory_fraction,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "cuda_launch_blocking": os.environ.get("CUDA_LAUNCH_BLOCKING"),
        "cuda_device_max_connections": os.environ.get(
            "CUDA_DEVICE_MAX_CONNECTIONS"
        ),
    }
    write_report(report_path, report)

    torch: Any = None
    try:
        import torch as imported_torch

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

        if hasattr(torch.backends, "cuda") and hasattr(torch.backends.cuda, "matmul"):
            torch.backends.cuda.matmul.allow_tf32 = False

        props = torch.cuda.get_device_properties(0)
        free_bytes, total_bytes = torch.cuda.mem_get_info(0)
        report["device"] = {
            "index": 0,
            "name": props.name,
            "compute_capability": [props.major, props.minor],
            "total_memory_bytes": int(total_bytes),
            "free_memory_before_bytes": int(free_bytes),
            "multiprocessor_count": int(props.multi_processor_count),
            "bf16_supported": bool(torch.cuda.is_bf16_supported()),
        }
        if not report["device"]["bf16_supported"]:
            raise RuntimeError("torch.cuda.is_bf16_supported() returned False")

        # These are bounded MLP-like F.linear shapes, not a stress test.
        stages = [
            {"name": "small", "m": 32, "k": 1024, "n": 4096},
            {"name": "prompt_like", "m": 256, "k": 1024, "n": 4096},
            {"name": "wide", "m": 128, "k": 2048, "n": 8192},
        ]
        report["stages"] = []
        generator = torch.Generator(device="cuda")
        generator.manual_seed(20260727)

        for stage in stages:
            name = stage["name"]
            m, k, n = stage["m"], stage["k"], stage["n"]
            report["status"] = f"running_{name}"
            write_report(report_path, report)
            print(f"stage {name}: F.linear [{m},{k}] x [{n},{k}] BF16", flush=True)

            x = torch.randn(
                (m, k),
                dtype=torch.bfloat16,
                device="cuda",
                generator=generator,
            )
            weight = torch.randn(
                (n, k),
                dtype=torch.bfloat16,
                device="cuda",
                generator=generator,
            )
            torch.cuda.synchronize(0)

            started = time.perf_counter()
            checksum = 0.0
            for _ in range(args.repeats):
                y = torch.nn.functional.linear(x, weight)
                checksum += float(y[0, 0].float().item())
                torch.cuda.synchronize(0)
            elapsed = time.perf_counter() - started

            stage_result = {
                **stage,
                "dtype": "bfloat16",
                "repeats": args.repeats,
                "seconds": round(elapsed, 6),
                "checksum": round(checksum, 6),
                "output_shape": list(y.shape),
            }
            report["stages"].append(stage_result)
            report["memory_bytes"] = {
                "allocated": int(torch.cuda.memory_allocated(0)),
                "reserved": int(torch.cuda.memory_reserved(0)),
                "max_allocated": int(torch.cuda.max_memory_allocated(0)),
                "max_reserved": int(torch.cuda.max_memory_reserved(0)),
            }
            write_report(report_path, report)
            del y, x, weight
            torch.cuda.empty_cache()
            torch.cuda.synchronize(0)

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
        if torch is not None:
            try:
                report["memory_at_failure_bytes"] = {
                    "allocated": int(torch.cuda.memory_allocated(0)),
                    "reserved": int(torch.cuda.memory_reserved(0)),
                    "max_allocated": int(torch.cuda.max_memory_allocated(0)),
                    "max_reserved": int(torch.cuda.max_memory_reserved(0)),
                }
            except BaseException:
                pass
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
