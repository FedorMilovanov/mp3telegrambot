#!/usr/bin/env python3
"""Run one small, synchronous CUDA probation stage and write a JSON report."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def base_report(stage: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "stage": stage,
        "status": "starting",
        "started_unix": time.time(),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "cuda_launch_blocking": os.environ.get("CUDA_LAUNCH_BLOCKING"),
        "pytorch_no_cuda_memory_caching": os.environ.get(
            "PYTORCH_NO_CUDA_MEMORY_CACHING"
        ),
    }


def initialize_cuda(torch: Any, report: dict[str, Any]) -> Any:
    report["torch_version"] = torch.__version__
    report["torch_cuda_build"] = torch.version.cuda
    report["cuda_available"] = bool(torch.cuda.is_available())
    if not report["cuda_available"]:
        raise RuntimeError("torch.cuda.is_available() returned False")

    torch.cuda.init()
    torch.cuda.set_device(0)
    torch.cuda.synchronize(0)

    properties = torch.cuda.get_device_properties(0)
    free_bytes, total_bytes = torch.cuda.mem_get_info(0)
    report["device"] = {
        "index": 0,
        "name": properties.name,
        "compute_capability": [properties.major, properties.minor],
        "total_memory_bytes": int(total_bytes),
        "free_memory_bytes": int(free_bytes),
        "multiprocessor_count": int(properties.multi_processor_count),
    }

    set_fraction = getattr(torch.cuda, "set_per_process_memory_fraction", None)
    if callable(set_fraction):
        set_fraction(0.10, device=0)
        report["memory_fraction_limit"] = 0.10
    else:
        report["memory_fraction_limit"] = None

    return torch.device("cuda:0")


def stage_init(torch: Any, device: Any, report: dict[str, Any]) -> None:
    del device
    torch.cuda.synchronize(0)
    report["checks"] = {"context_init": "passed"}


def stage_memory(torch: Any, device: Any, report: dict[str, Any]) -> None:
    elements = 16 * 1024 * 1024
    expected_first = 0
    expected_last = elements - 1

    host = torch.arange(elements, dtype=torch.int32)
    gpu = host.to(device, non_blocking=False)
    torch.cuda.synchronize(0)
    returned = gpu.cpu()
    torch.cuda.synchronize(0)

    first = int(returned[0].item())
    last = int(returned[-1].item())
    sample_indexes = [0, 1, 1024, elements // 2, elements - 2, elements - 1]
    mismatches = [
        index
        for index in sample_indexes
        if int(returned[index].item()) != index
    ]
    if first != expected_first or last != expected_last or mismatches:
        raise RuntimeError(
            "CUDA memory round-trip mismatch: "
            f"first={first}, last={last}, samples={mismatches}"
        )

    report["checks"] = {
        "allocation_bytes": int(elements * 4),
        "host_to_device": "passed",
        "device_to_host": "passed",
        "sample_mismatches": mismatches,
    }
    del returned, gpu, host
    torch.cuda.empty_cache()
    torch.cuda.synchronize(0)


def run_matmul(
    torch: Any,
    device: Any,
    report: dict[str, Any],
    *,
    dtype: Any,
    dtype_name: str,
    size: int,
    iterations: int,
) -> None:
    if dtype_name == "float16":
        supported = torch.cuda.get_device_capability(0)[0] >= 5
        if not supported:
            raise RuntimeError("float16 CUDA matmul is not supported")

    if hasattr(torch.backends, "cuda") and hasattr(torch.backends.cuda, "matmul"):
        torch.backends.cuda.matmul.allow_tf32 = False

    left = torch.ones((size, size), dtype=dtype, device=device)
    right = torch.ones((size, size), dtype=dtype, device=device)
    expected = float(size)
    timings: list[float] = []

    for iteration in range(iterations):
        started = time.perf_counter()
        result = left @ right
        torch.cuda.synchronize(0)
        elapsed = time.perf_counter() - started
        timings.append(elapsed)

        probes = result[
            torch.tensor([0, size // 2, size - 1], device=device),
            torch.tensor([0, size // 2, size - 1], device=device),
        ].float().cpu()
        values = [float(item) for item in probes.tolist()]
        if any(not math.isfinite(value) for value in values):
            raise RuntimeError(
                f"{dtype_name} matmul produced a non-finite probe: {values}"
            )
        tolerance = 0.0 if dtype_name == "float32" else 1.0
        if any(abs(value - expected) > tolerance for value in values):
            raise RuntimeError(
                f"{dtype_name} matmul mismatch at iteration {iteration}: "
                f"expected={expected}, values={values}"
            )
        del result, probes

    report["checks"] = {
        "dtype": dtype_name,
        "matrix_size": size,
        "iterations": iterations,
        "expected_probe_value": expected,
        "min_iteration_seconds": min(timings),
        "max_iteration_seconds": max(timings),
        "total_kernel_seconds": sum(timings),
    }
    del right, left
    torch.cuda.empty_cache()
    torch.cuda.synchronize(0)


def stage_sustained(
    torch: Any,
    device: Any,
    report: dict[str, Any],
    duration_seconds: float,
) -> None:
    size = 1536
    left = torch.ones((size, size), dtype=torch.float32, device=device)
    right = torch.ones((size, size), dtype=torch.float32, device=device)
    expected = float(size)
    deadline = time.perf_counter() + duration_seconds
    iterations = 0
    started = time.perf_counter()

    while time.perf_counter() < deadline:
        result = left @ right
        torch.cuda.synchronize(0)
        value = float(result[0, 0].item())
        if not math.isfinite(value) or value != expected:
            raise RuntimeError(
                "sustained matmul probe mismatch: "
                f"expected={expected}, value={value}"
            )
        iterations += 1
        del result

    elapsed = time.perf_counter() - started
    report["checks"] = {
        "dtype": "float32",
        "matrix_size": size,
        "iterations": iterations,
        "requested_seconds": duration_seconds,
        "elapsed_seconds": elapsed,
    }
    del right, left
    torch.cuda.empty_cache()
    torch.cuda.synchronize(0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("init", "memory", "fp32", "fp16", "sustained"),
        required=True,
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=15.0)
    args = parser.parse_args()

    report_path = args.report.expanduser().resolve()
    report = base_report(args.stage)
    write_report(report_path, report)

    try:
        import torch

        device = initialize_cuda(torch, report)
        report["status"] = "initialized"
        write_report(report_path, report)

        started = time.perf_counter()
        if args.stage == "init":
            stage_init(torch, device, report)
        elif args.stage == "memory":
            stage_memory(torch, device, report)
        elif args.stage == "fp32":
            run_matmul(
                torch,
                device,
                report,
                dtype=torch.float32,
                dtype_name="float32",
                size=1024,
                iterations=12,
            )
        elif args.stage == "fp16":
            run_matmul(
                torch,
                device,
                report,
                dtype=torch.float16,
                dtype_name="float16",
                size=1024,
                iterations=12,
            )
        else:
            stage_sustained(
                torch,
                device,
                report,
                duration_seconds=max(1.0, args.duration_seconds),
            )

        report["elapsed_seconds"] = time.perf_counter() - started
        report["status"] = "passed"
        report["finished_unix"] = time.time()
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except BaseException as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()[-8000:]
        report["finished_unix"] = time.time()
        write_report(report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
