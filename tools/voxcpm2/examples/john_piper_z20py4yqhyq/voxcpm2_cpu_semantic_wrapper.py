#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility wrapper that hardens the existing VoxCPM2 CPU renderer."""
from __future__ import annotations

import inspect
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any


def log(message: str) -> None:
    print(f"[VoxCPM2-HARDENED] {message}", flush=True)


def _load_prompt_texts() -> dict[str, str]:
    path = Path(os.environ.get("VOXCPM_PROMPT_TEXTS_JSON", "")).expanduser()
    if not path.is_file():
        raise RuntimeError("VOXCPM_PROMPT_TEXTS_JSON не найден.")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    result = {key: str(payload.get(key) or "").strip() for key in ("extended", "composite")}
    if not all(result.values()):
        raise RuntimeError("Не заполнены расшифровки voice reference.")
    return result


def _filter_kwargs(function: Any, values: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return values
    if any(item.kind == inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values()):
        return values
    return {key: value for key, value in values.items() if key in signature.parameters}


def _install_voxcpm_patch(prompt_texts: dict[str, str]) -> None:
    from voxcpm import VoxCPM

    original_from_pretrained = VoxCPM.from_pretrained

    def hardened_from_pretrained(*args: Any, **kwargs: Any) -> Any:
        requested = dict(kwargs)
        requested["load_denoiser"] = True
        denoiser_loaded = True
        try:
            model = original_from_pretrained(*args, **_filter_kwargs(original_from_pretrained, requested))
            log("внешний denoiser загружен")
        except Exception as exc:
            denoiser_loaded = False
            fallback = dict(kwargs)
            fallback["load_denoiser"] = False
            log(f"denoiser недоступен ({type(exc).__name__}); продолжаю с reference cleanup и semantic QA")
            model = original_from_pretrained(*args, **_filter_kwargs(original_from_pretrained, fallback))

        original_generate = model.generate

        def hardened_generate(*args2: Any, **kwargs2: Any) -> Any:
            reference = str(kwargs2.pop("reference_wav_path", "") or kwargs2.get("prompt_wav_path", "") or "")
            profile = "composite" if "composite" in Path(reference).name.casefold() else "extended"
            values = dict(kwargs2)
            values["text"] = str(values.get("text") or "").strip()
            values["cfg_value"] = max(1.9, float(values.get("cfg_value", 2.0)))
            values["normalize"] = True
            values["denoise"] = bool(denoiser_loaded)
            values["retry_badcase"] = True
            values["retry_badcase_max_times"] = 3
            values["retry_badcase_ratio_threshold"] = 4.8

            parameters = {}
            try:
                parameters = inspect.signature(original_generate).parameters
            except (TypeError, ValueError):
                parameters = {}
            if "prompt_wav_path" in parameters or any(
                item.kind == inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            ):
                values["prompt_wav_path"] = reference
                values["prompt_text"] = prompt_texts[profile]
            else:
                values["reference_wav_path"] = reference
            log(f"{profile}: prompt transcript + retry_badcase + denoise={denoiser_loaded}")
            return original_generate(*args2, **_filter_kwargs(original_generate, values))

        model.generate = hardened_generate
        return model

    VoxCPM.from_pretrained = hardened_from_pretrained


def main() -> None:
    prompt_texts = _load_prompt_texts()
    _install_voxcpm_patch(prompt_texts)
    original = Path(os.environ.get("VOXCPM_ORIGINAL_RENDERER", "")).expanduser().resolve()
    if not original.is_file():
        raise RuntimeError(f"Исходный production renderer не найден: {original}")
    sys.argv[0] = str(original)
    runpy.run_path(str(original), run_name="__main__")


if __name__ == "__main__":
    main()
