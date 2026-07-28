#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bootstrap audio-only repair for projects whose first render never wrote a manifest.

The production runtime writes ``output/manifest.json`` only after the final master.
A project may therefore already contain source.mp4, translated segments, subtitles and
other reusable work while lacking the manifest because the original render failed at
or before mastering. Audio repair must be able to salvage that work without calling
translation or title generation again.
"""
from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import subprocess
from typing import Any

from services.dub_studio import utc_now
from tools.voxcpm2 import generic_audio_repair_runtime as repair_runtime
from tools.voxcpm2 import generic_project_runtime as production
from tools.voxcpm2 import semantic_tts_guard_v4


class RepairSubprocessDiagnostics:
    """Tee child output and raise with its real final error instead of only code 1."""

    def __init__(self, real: Any, log_path: Path) -> None:
        self._real = real
        self._log_path = log_path

    @staticmethod
    def _label(command: Any) -> str:
        parts = [str(part) for part in command] if isinstance(command, (list, tuple)) else [str(command)]
        if len(parts) > 1:
            return Path(parts[1]).name
        return Path(parts[0]).name if parts else "unknown"

    @staticmethod
    def _result_tail(result: Any) -> str:
        text = "\n".join(
            value for value in (str(getattr(result, "stdout", "") or ""), str(getattr(result, "stderr", "") or "")) if value
        )
        return text[-12000:].strip()

    def run(self, command: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # The Quality guard invokes this path with cwd/env/check only. Preserve
        # compatibility for any unusual explicit pipe/timeout call by delegating,
        # while still turning a non-zero result into an exact diagnostic.
        unsupported = {"stdout", "stderr", "capture_output", "input", "timeout"}.intersection(kwargs)
        if args or unsupported:
            result = self._real.run(command, *args, **kwargs)
            if int(getattr(result, "returncode", 1)) != 0:
                tail = self._result_tail(result)
                raise RuntimeError(
                    f"Дочерний этап {self._label(command)} завершился с кодом "
                    f"{result.returncode}." + (f"\n\n{tail}" if tail else "")
                )
            return result

        call_kwargs = dict(kwargs)
        call_kwargs.pop("check", None)
        call_kwargs.pop("text", None)
        call_kwargs.pop("encoding", None)
        call_kwargs.pop("errors", None)

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        label = self._label(command)
        tail_lines: deque[str] = deque(maxlen=260)
        with self._log_path.open("a", encoding="utf-8", errors="replace") as log_file:
            header = f"\n=== CHILD START: {label} ===\n"
            print(header.rstrip(), flush=True)
            log_file.write(header)
            log_file.flush()
            proc = self._real.Popen(
                command,
                stdout=self._real.PIPE,
                stderr=self._real.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                **call_kwargs,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end="", flush=True)
                log_file.write(line)
                log_file.flush()
                tail_lines.append(line)
            return_code = int(proc.wait())
            footer = f"=== CHILD END: {label}; code={return_code} ===\n"
            print(footer.rstrip(), flush=True)
            log_file.write(footer)
            log_file.flush()

        tail = "".join(tail_lines)[-12000:].strip()
        if return_code != 0:
            raise RuntimeError(
                f"Дочерний этап {label} завершился с кодом {return_code}. "
                f"Полный журнал: {self._log_path}"
                + (f"\n\nПоследние строки дочернего процесса:\n{tail}" if tail else "")
            )
        return subprocess.CompletedProcess(command, return_code, stdout=tail, stderr="")


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} должен содержать JSON-объект.")
    return payload


def _read_title(root: Path, request: dict[str, Any], project_id: str) -> str:
    title_path = root / "russian_title.txt"
    title = ""
    if title_path.is_file():
        title = title_path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not title:
        title = str(request.get("russian_title") or request.get("title") or "").strip()
    return production.safe_russian_filename(
        title,
        fallback=f"Русский дубляж {project_id}",
    )


def _segment_count(root: Path) -> int:
    path = root / "segments_ru_final.json"
    if not path.is_file():
        raise RuntimeError(
            "Нельзя восстановить manifest.json: отсутствует segments_ru_final.json. "
            "Этот проект не дошёл до готового перевода; используйте /dubrun PROJECT_ID."
        )
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(
            "Нельзя восстановить manifest.json: segments_ru_final.json пуст или повреждён."
        )
    return len([item for item in payload if isinstance(item, dict)])


def _support_entry(
    entries: list[dict[str, Any]],
    path: Path,
    *,
    filename: str,
    label: str,
) -> None:
    if path.is_file():
        entries.append(
            production._telegram_entry(
                path,
                filename=filename,
                label=label,
            )
        )


def build_recovered_manifest(
    root: Path,
    request: dict[str, Any],
    project_id: str,
) -> dict[str, Any]:
    """Build the minimum complete manifest needed by repair and /dubsend."""
    source = root / "source" / "source.mp4"
    if not source.is_file():
        raise RuntimeError(
            "Нельзя восстановить manifest.json: отсутствует source/source.mp4. "
            "Используйте /dubrun PROJECT_ID."
        )

    segments = _segment_count(root)
    output_dir = root / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    title = _read_title(root, request, project_id)
    video_id = str(request.get("video_id") or project_id)
    named_mixed = output_dir / f"{title} — русский дубляж.mp4"
    named_russian = output_dir / f"{title} — только русский голос.mp4"
    russian_srt = output_dir / "russian_subtitles.srt"
    source_srt = output_dir / "source_subtitles.srt"
    translation = output_dir / "russian_translation.txt"
    qa = output_dir / "translation_qa.txt"
    russian_timeline = root / "audio" / f"{video_id}_ru_timeline.wav"

    telegram_outputs = [
        production._telegram_entry(
            named_mixed,
            filename=named_mixed.name,
            label="Готовый ролик: оригинал 18%, русский голос",
            primary=True,
            video=True,
        ),
        production._telegram_entry(
            named_russian,
            filename=named_russian.name,
            label="Версия только с русским голосом",
            video=True,
            send_default=False,
        ),
    ]
    _support_entry(
        telegram_outputs,
        russian_srt,
        filename=f"{title} — русские субтитры.srt",
        label="Русские субтитры",
    )
    _support_entry(
        telegram_outputs,
        translation,
        filename=f"{title} — перевод.txt",
        label="Итоговый русский перевод",
    )
    _support_entry(
        telegram_outputs,
        source_srt,
        filename=f"{title} — исходные субтитры.srt",
        label="Исходные субтитры",
    )
    _support_entry(
        telegram_outputs,
        qa,
        filename=f"{title} — проверка перевода.txt",
        label="Отчёт контроля перевода",
    )

    return {
        "schema_version": 2,
        "project_id": project_id,
        "video_id": video_id,
        "source_url": str(request.get("source_url") or ""),
        "russian_title": title,
        "translation_mode": str(request.get("translation_mode") or "unknown"),
        "translation_model": "reused",
        "translation_passes": 0,
        "original_level": float(request.get("original_level") or 0.18),
        "russian_delay_ms": int(request.get("russian_delay_ms") or 420),
        "segments": segments,
        "phase": "audio_repair_manifest_recovered",
        "manifest_recovered": True,
        "manifest_recovered_at": utc_now(),
        "translation_reused": True,
        "gemini_called": False,
        "outputs": {
            "mixed": str(named_mixed),
            "russian_only": str(named_russian),
            "russian_srt": str(russian_srt),
            "source_srt": str(source_srt),
            "translation": str(translation),
            "qa": str(qa),
            "russian_timeline": str(russian_timeline),
        },
        "telegram_outputs": telegram_outputs,
    }


def ensure_repair_manifest(
    root: Path,
    request: dict[str, Any],
    project_id: str,
) -> dict[str, Any]:
    manifest_path = root / "output" / "manifest.json"
    if manifest_path.is_file():
        return _load_json_object(manifest_path)

    manifest = build_recovered_manifest(root, request, project_id)
    production.save_json(manifest_path, manifest)
    production.log(
        "=== AUDIO REPAIR: manifest.json отсутствовал и восстановлен из локальных "
        "артефактов без Gemini ==="
    )
    return manifest


def install_repair_diagnostics(root: Path) -> Path:
    log_path = root / "output" / "audio_repair_child.log"
    current = semantic_tts_guard_v4._REAL_SUBPROCESS
    if not isinstance(current, RepairSubprocessDiagnostics):
        semantic_tts_guard_v4._REAL_SUBPROCESS = RepairSubprocessDiagnostics(
            current,
            log_path,
        )
    return log_path


def main() -> None:
    project_id = production.current_project_id()
    root = production.project_root(project_id)
    request = production.load_request(root)
    ensure_repair_manifest(root, request, project_id)
    log_path = install_repair_diagnostics(root)
    production.log(f"AUDIO REPAIR child log: {log_path}")
    repair_runtime.main()


if __name__ == "__main__":
    main()
