#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Semantic guard for VoxCPM2 dubbing.

The legacy renderer judged candidates only by duration, silence and clipping. This
adapter keeps the existing production pipeline, but routes VoxCPM2 synthesis
through a hardened wrapper, verifies the spoken Russian with local Whisper, and
re-renders only failed segments with new deterministic seeds.
"""
from __future__ import annotations

import collections
import difflib
import json
import math
import os
import re
import subprocess as _subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

_GUARD_VERSION = "semantic-tts-guard-v1"
_SYNTH_NAME = "voxcpm2_cpu_shorts_production.py"
_WRAPPER_NAME = "voxcpm2_cpu_semantic_wrapper.py"
_LOCK = threading.RLock()
_WHISPER_MODELS: dict[str, Any] = {}
_REAL_SUBPROCESS = _subprocess


def log(message: str) -> None:
    print(f"[TTS-QA] {message}", flush=True)


def _flag_value(command: Sequence[str], flag: str) -> str:
    try:
        index = list(command).index(flag)
    except ValueError as exc:
        raise RuntimeError(f"В команде VoxCPM2 отсутствует обязательный параметр {flag}.") from exc
    if index + 1 >= len(command):
        raise RuntimeError(f"После {flag} отсутствует значение.")
    return str(command[index + 1])


def _replace_flag(command: list[str], flag: str, value: str) -> None:
    try:
        index = command.index(flag)
    except ValueError:
        command.extend([flag, value])
    else:
        if index + 1 >= len(command):
            command.append(value)
        else:
            command[index + 1] = value


def _is_voxcpm_synth(command: Any) -> bool:
    if not isinstance(command, (list, tuple)):
        return False
    return any(Path(str(part)).name.casefold() == _SYNTH_NAME.casefold() for part in command)


def sanitize_tts_text(value: str) -> str:
    """Preserve every word while removing continuation punctuation for TTS."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    # Leading/trailing ellipses make autoregressive TTS continue the English
    # reference instead of stopping at the requested Russian sentence.
    text = re.sub(r"^[\s\u00a0]*(?:[«„“\"']\s*)?(?:\.{2,}|…)+\s*", "", text)
    text = re.sub(r"\s*(?:\.{2,}|…)+(?:\s*[»”\"'])?\s*$", "", text)
    text = re.sub(r"(?:\.{3,}|…)", ",", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r",{2,}", ",", text).strip(" ,")
    if text and text[-1] not in ".!?;:»:”\"'":
        text += "."
    return text


def normalize_asr_text(value: str) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def compare_spoken_text(target: str, heard: str, language: str = "", language_probability: float = 0.0) -> dict[str, Any]:
    target_norm = normalize_asr_text(target)
    heard_norm = normalize_asr_text(heard)
    target_tokens = target_norm.split()
    heard_tokens = heard_norm.split()
    target_counts = collections.Counter(target_tokens)
    heard_counts = collections.Counter(heard_tokens)
    overlap = sum((target_counts & heard_counts).values())
    recall = overlap / max(1, len(target_tokens))
    precision = overlap / max(1, len(heard_tokens))
    sequence = difflib.SequenceMatcher(None, target_norm, heard_norm).ratio() if heard_norm else 0.0

    letters = re.findall(r"[a-zа-я]", heard_norm, flags=re.I)
    latin = re.findall(r"[a-z]", heard_norm, flags=re.I)
    latin_ratio = len(latin) / max(1, len(letters))
    lang = str(language or "").casefold()
    foreign_language = bool(lang and lang not in {"ru", "uk", "be"} and float(language_probability or 0.0) >= 0.72)
    excessive_extra = len(heard_tokens) > max(len(target_tokens) + 7, math.ceil(len(target_tokens) * 1.55))

    passed = bool(
        heard_norm
        and not foreign_language
        and latin_ratio <= 0.12
        and not excessive_extra
        and (
            sequence >= 0.54
            or (recall >= 0.68 and precision >= 0.45)
            or (recall >= 0.80 and len(target_tokens) <= 8)
        )
    )
    return {
        "passed": passed,
        "target": target,
        "heard": heard,
        "language": language,
        "language_probability": round(float(language_probability or 0.0), 4),
        "sequence_similarity": round(sequence, 4),
        "token_recall": round(recall, 4),
        "token_precision": round(precision, 4),
        "latin_ratio": round(latin_ratio, 4),
        "foreign_language": foreign_language,
        "excessive_extra": excessive_extra,
    }


def _read_pcm_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        sample_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width == 2:
        data = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        values = raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16)
        values = np.where(values & 0x800000, values - 0x1000000, values)
        data = values.astype(np.float32) / 8388608.0
    elif sample_width == 4:
        data = np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise RuntimeError(f"Неподдерживаемая разрядность WAV: {sample_width * 8} бит.")
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, int(sample_rate)


def acoustic_check(path: Path) -> dict[str, Any]:
    samples, sample_rate = _read_pcm_mono(path)
    if not len(samples):
        return {"passed": False, "reason": "empty_audio"}
    finite = bool(np.isfinite(samples).all())
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64))) + 1e-12))
    peak = float(np.max(np.abs(samples)))
    clipping = float(np.mean(np.abs(samples) >= 0.995))
    zcr = float(np.mean(np.signbit(samples[1:]) != np.signbit(samples[:-1]))) if len(samples) > 1 else 0.0
    duration = len(samples) / max(1, sample_rate)
    passed = bool(finite and duration >= 0.30 and rms >= 0.002 and clipping <= 0.004 and zcr <= 0.32)
    return {
        "passed": passed,
        "duration": round(duration, 4),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "clipping_ratio": round(clipping, 7),
        "zero_crossing_rate": round(zcr, 5),
        "finite": finite,
    }


def _whisper_model(name: str) -> Any:
    with _LOCK:
        if name in _WHISPER_MODELS:
            return _WHISPER_MODELS[name]
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Для обязательной проверки речи установите faster-whisper из requirements.txt."
            ) from exc
        log(f"загружаю Whisper {name} для проверки произнесённого текста")
        model = WhisperModel(name, device="cpu", compute_type="int8")
        _WHISPER_MODELS[name] = model
        return model


def _transcribe(path: Path, *, language: str | None = None) -> tuple[str, str, float]:
    model_name = os.getenv("DUB_TTS_QA_MODEL", "small").strip() or "small"
    model = _whisper_model(model_name)
    segments, info = model.transcribe(
        str(path),
        language=language,
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=False,
        word_timestamps=False,
        temperature=0.0,
    )
    text = " ".join(str(item.text).strip() for item in segments if str(item.text).strip()).strip()
    return text, str(getattr(info, "language", "") or ""), float(getattr(info, "language_probability", 0.0) or 0.0)


def _clean_reference(source: Path, destination: Path, *, max_seconds: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-t", f"{max_seconds:.3f}", "-vn", "-ac", "1", "-ar", "16000",
        "-af", "highpass=f=70,lowpass=f=7600,afftdn=nf=-28,afade=t=in:st=0:d=0.015,areverse,afade=t=in:st=0:d=0.020,areverse,loudnorm=I=-20:LRA=7:TP=-2",
        str(destination),
    ]
    result = _REAL_SUBPROCESS.run(command, stdout=_REAL_SUBPROCESS.PIPE, stderr=_REAL_SUBPROCESS.PIPE, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Не удалось очистить голосовой референс: {source}")


def _prepare_guarded_segments(source: Path, destination: Path) -> list[dict[str, Any]]:
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Пустой segments JSON перед VoxCPM2.")
    guarded: list[dict[str, Any]] = []
    for index, raw in enumerate(payload, start=1):
        item = dict(raw)
        original = str(item.get("text") or "").strip()
        spoken = sanitize_tts_text(original)
        if not spoken:
            raise RuntimeError(f"После TTS-нормализации опустела реплика #{index}.")
        item["display_text"] = original
        item["text"] = spoken
        item["semantic_guard_version"] = _GUARD_VERSION
        guarded.append(item)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(guarded, ensure_ascii=False, indent=2), encoding="utf-8")
    return guarded


def _extract_clip(source: Path, destination: Path, start: float, duration: float) -> None:
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.30, duration):.3f}",
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(destination),
    ]
    result = _REAL_SUBPROCESS.run(command, stdout=_REAL_SUBPROCESS.PIPE, stderr=_REAL_SUBPROCESS.PIPE, check=False)
    if result.returncode != 0 or not destination.is_file():
        raise RuntimeError(f"Не удалось извлечь аудио реплики для QA: {destination.name}")


def verify_timeline(timeline: Path, segments: list[dict[str, Any]], report_path: Path) -> tuple[list[int], dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    failed: list[int] = []
    with tempfile.TemporaryDirectory(prefix="tts-semantic-qa-") as temp_raw:
        temp = Path(temp_raw)
        for item in segments:
            segment_id = int(item["id"])
            delay = max(0, int(item.get("start_delay_ms", 0))) / 1000.0
            start = float(item["start"]) + delay
            duration = max(0.35, float(item["end"]) - float(item["start"]))
            clip = temp / f"segment_{segment_id:03d}.wav"
            _extract_clip(timeline, clip, start, duration)
            acoustic = acoustic_check(clip)
            heard, language, probability = _transcribe(clip, language=None)
            semantic = compare_spoken_text(str(item["text"]), heard, language, probability)
            passed = bool(acoustic.get("passed") and semantic.get("passed"))
            check = {
                "id": segment_id,
                "passed": passed,
                "acoustic": acoustic,
                "semantic": semantic,
            }
            checks.append(check)
            if not passed:
                failed.append(segment_id)
    report = {
        "schema_version": 1,
        "guard_version": _GUARD_VERSION,
        "timeline": str(timeline),
        "passed": not failed,
        "failed_segment_ids": failed,
        "segments": checks,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return failed, report


def _invalidate_legacy_checkpoints(work_dir: Path) -> None:
    marker = work_dir / "semantic_guard.marker.json"
    current = None
    if marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = None
    if isinstance(current, dict) and current.get("guard_version") == _GUARD_VERSION:
        return
    for path in (work_dir / "checkpoints").glob("segment_*.json"):
        path.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)


def _retarget_checkpoints(work_dir: Path, *, good_ids: Iterable[int], failed_ids: Iterable[int], new_base_seed: int) -> None:
    good = {int(value) for value in good_ids}
    failed = {int(value) for value in failed_ids}
    for segment_id in good:
        path = work_dir / "checkpoints" / f"segment_{segment_id:02d}.json"
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        signature = payload.get("signature") if isinstance(payload, dict) else None
        if isinstance(signature, dict):
            signature["base_seed"] = int(new_base_seed)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for segment_id in failed:
        (work_dir / "checkpoints" / f"segment_{segment_id:02d}.json").unlink(missing_ok=True)
        for directory in ("segments_clean", "segments_fitted"):
            for path in (work_dir / directory).glob(f"{segment_id:02d}_*.wav"):
                path.unlink(missing_ok=True)


def _run_guarded_synth(command: Sequence[str], *args: Any, **kwargs: Any) -> Any:
    original_command = [str(part) for part in command]
    env = dict(kwargs.get("env") or os.environ)
    work_dir = Path(_flag_value(original_command, "--work-dir")).resolve()
    timeline = Path(_flag_value(original_command, "--output")).resolve()
    segments_source = Path(_flag_value(original_command, "--segments-json")).resolve()
    extended_source = Path(_flag_value(original_command, "--extended-reference")).resolve()
    composite_source = Path(_flag_value(original_command, "--composite-reference")).resolve()
    base_seed = int(_flag_value(original_command, "--base-seed"))

    guard_dir = work_dir / "semantic_guard"
    guard_dir.mkdir(parents=True, exist_ok=True)
    guarded_segments_path = guard_dir / "segments_guarded.json"
    segments = _prepare_guarded_segments(segments_source, guarded_segments_path)

    extended = guard_dir / "extended_reference_clean.wav"
    composite = guard_dir / "composite_reference_clean.wav"
    _clean_reference(extended_source, extended, max_seconds=14.0)
    _clean_reference(composite_source, composite, max_seconds=12.0)

    # Exact reference transcripts prevent the English prompt audio from leaking
    # into the Russian continuation. Whisper is also the required final QA gate.
    extended_text, _, _ = _transcribe(extended, language="en")
    composite_text, _, _ = _transcribe(composite, language="en")
    if not extended_text or not composite_text:
        raise RuntimeError("Whisper не смог распознать текст голосового референса; синтез остановлен.")
    prompt_path = guard_dir / "reference_prompt_texts.json"
    prompt_path.write_text(
        json.dumps({"extended": extended_text, "composite": composite_text}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    wrapper = Path(__file__).resolve().parent / _WRAPPER_NAME
    if not wrapper.is_file():
        raise RuntimeError(f"Не найден hardened VoxCPM2 wrapper: {wrapper}")

    guarded_command = list(original_command)
    for index, part in enumerate(guarded_command):
        if Path(str(part)).name.casefold() == _SYNTH_NAME.casefold():
            guarded_command[index] = str(wrapper)
            break
    _replace_flag(guarded_command, "--segments-json", str(guarded_segments_path))
    _replace_flag(guarded_command, "--extended-reference", str(extended))
    _replace_flag(guarded_command, "--composite-reference", str(composite))
    env["VOXCPM_PROMPT_TEXTS_JSON"] = str(prompt_path)
    env["VOXCPM_ORIGINAL_RENDERER"] = str(Path(original_command[1]).resolve())
    env["VOXCPM_SEMANTIC_GUARD_VERSION"] = _GUARD_VERSION
    kwargs["env"] = env

    _invalidate_legacy_checkpoints(work_dir)
    max_rounds = max(1, min(4, int(os.getenv("DUB_TTS_QA_MAX_ROUNDS", "3") or "3")))
    all_ids = {int(item["id"]) for item in segments}
    last_report: dict[str, Any] = {}

    for round_index in range(max_rounds):
        round_seed = base_seed + round_index * 100_000
        _replace_flag(guarded_command, "--base-seed", str(round_seed))
        log(f"синтез, раунд {round_index + 1}/{max_rounds}, base_seed={round_seed}")
        result = _REAL_SUBPROCESS.run(guarded_command, *args, **kwargs)
        if int(getattr(result, "returncode", 1)) != 0:
            return result
        report_path = timeline.with_suffix(f".semantic_qa.round{round_index + 1}.json")
        failed, last_report = verify_timeline(timeline, segments, report_path)
        if not failed:
            (work_dir / "semantic_guard.marker.json").write_text(
                json.dumps({"guard_version": _GUARD_VERSION, "base_seed": round_seed}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            timeline.with_suffix(".semantic_qa.json").write_text(
                json.dumps(last_report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log("все реплики прошли акустическую и семантическую проверку")
            return result

        log(f"не прошли реплики {failed}; перегенерирую только их с новыми seed")
        next_seed = base_seed + (round_index + 1) * 100_000
        _retarget_checkpoints(
            work_dir,
            good_ids=all_ids - set(failed),
            failed_ids=failed,
            new_base_seed=next_seed,
        )

    timeline.with_suffix(".semantic_qa.json").write_text(
        json.dumps(last_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    raise RuntimeError(
        "VoxCPM2 не прошёл обязательную проверку произнесённого русского текста после "
        f"{max_rounds} раундов. Не приняты сегменты: {last_report.get('failed_segment_ids', [])}."
    )


class GuardedSubprocessProxy:
    """Module-like proxy that intercepts only the VoxCPM2 synthesis command."""

    def __init__(self, real: Any) -> None:
        self._real = real

    def run(self, command: Any, *args: Any, **kwargs: Any) -> Any:
        if _is_voxcpm_synth(command):
            return _run_guarded_synth(command, *args, **kwargs)
        return self._real.run(command, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def install() -> None:
    """Install into all already-loaded dubbing modules without changing flows."""
    proxy = GuardedSubprocessProxy(_REAL_SUBPROCESS)
    try:
        import tools.voxcpm2.generic_short_production as pipeline
        if not isinstance(getattr(pipeline, "subprocess", None), GuardedSubprocessProxy):
            pipeline.subprocess = proxy
    except Exception as exc:
        log(f"не удалось подключить guard к legacy pipeline: {type(exc).__name__}: {exc}")

    for module in list(sys.modules.values()):
        if module is None:
            continue
        file_name = Path(str(getattr(module, "__file__", "") or "")).name.casefold()
        if file_name not in {"generic_project_runtime.py", "generic_direct_runtime.py"}:
            continue
        if hasattr(module, "subprocess") and not isinstance(getattr(module, "subprocess"), GuardedSubprocessProxy):
            setattr(module, "subprocess", proxy)
    log("semantic VoxCPM2 guard активирован для Gemini MAX и готового SRT")
