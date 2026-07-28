#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VoxCPM2 semantic rescue entrypoint for persistent foreign-language leaks.

The normal Professional Audio renderer remains the source of timing, candidate
selection and checkpoint logic. This wrapper only adds the exact transcript of
the English voice reference to VoxCPM's prompt API, enables its internal bad-case
retry and appends a deterministic silent tail before final timing fit.
"""
from __future__ import annotations

import inspect
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any

import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def log(message: str) -> None:
    print(f"[VOXCPM2-SEMANTIC-RESCUE-V4.7] {message}", flush=True)


def _load_prompt_texts() -> dict[str, str]:
    path = Path(os.environ.get("VOXCPM_PROMPT_TEXTS_JSON", "")).expanduser()
    if not path.is_file():
        raise RuntimeError(f"Не найден JSON расшифровок voice reference: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    result = {
        profile: str(payload.get(profile) or "").strip()
        for profile in ("extended", "composite")
    }
    if not all(result.values()):
        raise RuntimeError("Расшифровки extended/composite voice reference пусты.")
    return result


def _parameters(function: Any) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(function).parameters)
    except (TypeError, ValueError):
        return {}


def _accepts_kwargs(parameters: dict[str, inspect.Parameter]) -> bool:
    return any(
        item.kind == inspect.Parameter.VAR_KEYWORD
        for item in parameters.values()
    )


def _filter_kwargs(function: Any, values: dict[str, Any]) -> dict[str, Any]:
    parameters = _parameters(function)
    if not parameters or _accepts_kwargs(parameters):
        return values
    return {key: value for key, value in values.items() if key in parameters}


def _install_semantic_patch(prompt_texts: dict[str, str]) -> None:
    from voxcpm import VoxCPM

    original_from_pretrained = VoxCPM.from_pretrained

    def rescued_from_pretrained(*args: Any, **kwargs: Any) -> Any:
        requested = dict(kwargs)
        requested["load_denoiser"] = False
        model = original_from_pretrained(
            *args,
            **_filter_kwargs(original_from_pretrained, requested),
        )
        original_generate = model.generate
        parameters = _parameters(original_generate)
        has_prompt_api = (
            "prompt_wav_path" in parameters
            or "prompt_text" in parameters
        )
        rescue_cfg = max(
            1.90,
            float(os.getenv("VOXCPM_RESCUE_CFG", "1.95") or "1.95"),
        )

        def rescued_generate(*args2: Any, **kwargs2: Any) -> Any:
            reference = str(
                kwargs2.pop("reference_wav_path", "")
                or kwargs2.get("prompt_wav_path", "")
                or ""
            )
            if not reference:
                raise RuntimeError("Semantic rescue не получил voice reference.")
            profile = (
                "composite"
                if "composite" in Path(reference).name.casefold()
                else "extended"
            )
            values = dict(kwargs2)
            target_text = str(values.get("text") or "").strip()
            if not target_text:
                raise RuntimeError("Semantic rescue получил пустой русский target.")
            values["text"] = target_text
            values["cfg_value"] = max(
                rescue_cfg,
                float(values.get("cfg_value", rescue_cfg)),
            )
            values["normalize"] = True
            values["denoise"] = False
            values["retry_badcase"] = True
            values["retry_badcase_max_times"] = 4
            values["retry_badcase_ratio_threshold"] = 4.2

            if has_prompt_api:
                if "prompt_wav_path" in parameters:
                    values["prompt_wav_path"] = reference
                if "prompt_text" in parameters:
                    values["prompt_text"] = prompt_texts[profile]
                if "reference_wav_path" in parameters:
                    values["reference_wav_path"] = reference
            else:
                values["reference_wav_path"] = reference
                if "reference_text" in parameters:
                    values["reference_text"] = prompt_texts[profile]

            log(
                f"{profile}: exact English prompt transcript + Russian target; "
                f"cfg={values['cfg_value']:.2f}; retry_badcase=4"
            )
            generated = original_generate(
                *args2,
                **_filter_kwargs(original_generate, values),
            )
            audio = np.asarray(generated, dtype=np.float32).reshape(-1)
            sample_rate = int(model.tts_model.sample_rate)
            tail = np.zeros(max(1, int(sample_rate * 0.160)), dtype=np.float32)
            return np.concatenate([audio, tail])

        model.generate = rescued_generate
        log(
            "локальная CPU-модель загружена; semantic prompt rescue и "
            "160 ms silent tail активированы"
        )
        return model

    VoxCPM.from_pretrained = rescued_from_pretrained


def main() -> None:
    prompt_texts = _load_prompt_texts()
    _install_semantic_patch(prompt_texts)
    renderer = Path(
        os.environ.get("VOXCPM_RESCUE_RENDERER", "")
    ).expanduser().resolve()
    if not renderer.is_file():
        raise RuntimeError(f"Professional rescue renderer не найден: {renderer}")
    sys.argv[0] = str(renderer)
    runpy.run_path(str(renderer), run_name="__main__")


if __name__ == "__main__":
    main()
