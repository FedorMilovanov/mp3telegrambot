#!/usr/bin/env python3
"""Run the no-cuDNN smoke while filtering unsupported VoxCPM kwargs."""
from __future__ import annotations

import inspect
import runpy
import sys
from pathlib import Path
from typing import Any

from voxcpm import VoxCPM


_ORIGINAL_GENERATE = VoxCPM.generate


def compatible_generate(self: Any, *args: Any, **kwargs: Any) -> Any:
    """Normalize one-attempt semantics and drop unsupported installed-version kwargs."""
    if kwargs.get("retry_badcase") is False:
        # VoxCPM uses this value as the total number of loop attempts, not merely retries.
        # A value of 0 skips inference entirely and leaves latent_pred undefined.
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


VoxCPM.generate = compatible_generate

TARGET = Path(__file__).with_name("voxcpm2_cuda_zero_shot_no_cudnn_smoke.py")
if not TARGET.is_file():
    raise SystemExit(f"missing target smoke script: {TARGET}")

runpy.run_path(str(TARGET), run_name="__main__")
