#!/usr/bin/env python3
"""Force VoxCPM2 CUDA runtime to FP16, disable cuDNN, and run one zero-shot attempt."""
from __future__ import annotations

import inspect
import runpy
import sys
from pathlib import Path
from typing import Any

import voxcpm.model.voxcpm2 as voxcpm2_module
from voxcpm import VoxCPM


_ORIGINAL_PICK_RUNTIME_DTYPE = voxcpm2_module.pick_runtime_dtype
_ORIGINAL_GENERATE = VoxCPM.generate


def force_cuda_fp16(device: str, configured_dtype: str) -> str:
    normalized = str(device).strip().lower()
    if normalized.startswith("cuda"):
        if str(configured_dtype).strip().lower() not in {"float16", "fp16"}:
            print(
                f"FP16 compatibility wrapper adjusted dtype {configured_dtype} -> float16",
                file=sys.stderr,
                flush=True,
            )
        return "float16"
    return _ORIGINAL_PICK_RUNTIME_DTYPE(device, configured_dtype)


def compatible_generate(self: Any, *args: Any, **kwargs: Any) -> Any:
    """Use one total attempt and drop only kwargs unsupported by the installed API."""
    if kwargs.get("retry_badcase") is False:
        kwargs["retry_badcase_max_times"] = 1
        print(
            "compatibility wrapper set retry_badcase_max_times=1 "
            "(one attempt, zero retries)",
            file=sys.stderr,
            flush=True,
        )

    try:
        signature = inspect.signature(self._generate)
        parameters = signature.parameters
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
    except (TypeError, ValueError):
        parameters = {}
        accepts_var_kwargs = False

    if not accepts_var_kwargs and parameters:
        unsupported = sorted(key for key in kwargs if key not in parameters)
        for key in unsupported:
            kwargs.pop(key, None)
        if unsupported:
            print(
                "compatibility wrapper removed unsupported VoxCPM kwargs: "
                + ", ".join(unsupported),
                file=sys.stderr,
                flush=True,
            )

    return _ORIGINAL_GENERATE(self, *args, **kwargs)


voxcpm2_module.pick_runtime_dtype = force_cuda_fp16
VoxCPM.generate = compatible_generate

TARGET = Path(__file__).with_name("voxcpm2_cuda_zero_shot_no_cudnn_smoke.py")
if not TARGET.is_file():
    raise SystemExit(f"missing target smoke script: {TARGET}")

runpy.run_path(str(TARGET), run_name="__main__")
