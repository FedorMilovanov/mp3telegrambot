from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.dub_projects import (
    DubProjectError,
    load_project,
    project_dir,
    record_preflight,
    utc_now,
)


SHORTS_MAX_SECONDS = 180.0
SHORTS_CONTAINER_TOLERANCE_SECONDS = 0.10
DEFAULT_CPU_PYTHON = Path(r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv\Scripts\python.exe")
DEFAULT_ARCHIVE_ROOT = Path(r"C:\AI-Archive\VoxCPM2-paused-RTX3060")


def _run(
    command: list[str],
    *,
    timeout: int = 180,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )


def _probe_local_media(path: Path) -> dict[str, Any]:
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size,start_time:"
            "stream=index,codec_type,codec_name,width,height,r_frame_rate,"
            "avg_frame_rate,start_time,color_transfer,color_primaries,"
            "color_space,pix_fmt,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        timeout=120,
    )
    if proc.returncode != 0:
        raise DubProjectError(
            "FFprobe не смог прочитать исходное видео: "
            + (proc.stderr or proc.stdout)[-800:]
        )
    try:
        payload = json.loads(proc.stdout)
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DubProjectError("FFprobe вернул некорректные метаданные исходника.") from exc
    if duration <= 0:
        raise DubProjectError("Не удалось определить положительную длительность исходника.")
    streams = [item for item in (payload.get("streams") or []) if isinstance(item, dict)]
    if not any(item.get("codec_type") == "video" for item in streams):
        raise DubProjectError("В исходном файле нет видеопотока.")
    if not any(item.get("codec_type") == "audio" for item in streams):
        raise DubProjectError(
            "В исходном файле нет аудиопотока: невозможно получить речь и голосовой референс."
        )
    return {
        "duration_seconds": duration,
        "format": payload.get("format") or {},
        "streams": streams,
        "probe_source": "ffprobe",
    }


def _probe_url(url: str) -> dict[str, Any]:
    proc = _run(
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            url,
        ],
        timeout=int(os.getenv("DUB_URL_PROBE_TIMEOUT_SEC", "180")),
    )
    if proc.returncode != 0:
        raise DubProjectError(
            "Не удалось получить метаданные ссылки: "
            + (proc.stderr or proc.stdout)[-1000:]
        )
    try:
        payload = json.loads(proc.stdout)
        duration = float(payload.get("duration") or 0)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DubProjectError("yt-dlp вернул некорректные метаданные ссылки.") from exc
    if duration <= 0:
        raise DubProjectError("У источника не удалось определить длительность.")
    acodec = str(payload.get("acodec") or "").lower()
    if acodec == "none":
        raise DubProjectError("У выбранного источника нет аудиопотока.")
    return {
        "duration_seconds": duration,
        "title": str(payload.get("title") or "").strip(),
        "uploader": str(payload.get("uploader") or payload.get("channel") or "").strip(),
        "webpage_url": str(payload.get("webpage_url") or url),
        "extractor": str(payload.get("extractor_key") or payload.get("extractor") or ""),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "fps": payload.get("fps"),
        "vcodec": payload.get("vcodec"),
        "acodec": payload.get("acodec"),
        "probe_source": "yt-dlp",
    }


def _looks_like_model_snapshot(path: Path) -> bool:
    return (
        path.is_dir()
        and (path / "config.json").is_file()
        and (
            (path / "model.safetensors").is_file()
            or any(path.glob("*.safetensors"))
            or any(path.glob("*.bin"))
        )
    )


def _find_model_snapshot(root: Path) -> Path | None:
    if _looks_like_model_snapshot(root):
        return root
    if not root.is_dir():
        return None
    common = [
        root / "models" / "voxcpm2-model-cache" / "models--openbmb--VoxCPM2",
        root / "models" / "voxcpm2-model-cache" / "models--OpenBMB--VoxCPM2",
    ]
    for candidate in common:
        snapshots = candidate / "snapshots"
        if snapshots.is_dir():
            for item in sorted(
                snapshots.iterdir(),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            ):
                if _looks_like_model_snapshot(item):
                    return item
    for config in root.rglob("config.json"):
        if _looks_like_model_snapshot(config.parent):
            return config.parent
    return None


def _sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").strip().encode("utf-8")).hexdigest()


def _canonical_json_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compile_python(python_executable: Path, path: Path) -> str | None:
    proc = _run(
        [str(python_executable), "-m", "py_compile", str(path)],
        timeout=120,
    )
    if proc.returncode == 0:
        return None
    return (proc.stderr or proc.stdout)[-1200:]


def _probe_cpu_runtime(cpu_python: Path) -> tuple[dict[str, Any] | None, str | None]:
    runtime_env = dict(os.environ)
    runtime_env.update(
        {
            "CUDA_VISIBLE_DEVICES": "-1",
            "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    code = (
        "import json, numpy, soundfile, torch, voxcpm; "
        "print(json.dumps({"
        "'python':__import__('sys').version.split()[0],"
        "'torch':torch.__version__,"
        "'cuda_available':bool(torch.cuda.is_available()),"
        "'voxcpm':getattr(voxcpm,'__version__','installed')"
        "}))"
    )
    try:
        proc = _run([str(cpu_python), "-c", code], timeout=180, env=runtime_env)
    except subprocess.TimeoutExpired:
        return None, "Проверка CPU-окружения VoxCPM2 превысила 180 секунд."
    if proc.returncode != 0:
        return None, "CPU-окружение VoxCPM2 не импортирует зависимости: " + (
            proc.stderr or proc.stdout
        )[-1200:]
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None, "CPU-окружение VoxCPM2 вернуло некорректный runtime-report."
    if payload.get("cuda_available") is not False:
        return payload, "CPU-окружение неожиданно видит CUDA; production остановлен."
    return payload, None


def _profile_for_duration(duration: float) -> tuple[str, bool]:
    is_short = duration <= SHORTS_MAX_SECONDS + SHORTS_CONTAINER_TOLERANCE_SECONDS
    return ("shorts_premium", True) if is_short else ("long_premium", False)


def _estimate_required_gib(duration_seconds: float, source_bytes: int = 0) -> float:
    source_gib = max(0, int(source_bytes or 0)) / (1024**3)
    hours = max(0.0, duration_seconds) / 3600.0
    return max(8.0, source_gib * 3.0 + 6.0 + hours * 4.0)


def run_project_preflight(project_id: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    manifest = load_project(project_id)
    root = project_dir(project_id)
    repository = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    blocking: list[str] = []
    warnings: list[str] = []

    if manifest.get("status") == "cancelled":
        blocking.append("Проект отменён.")

    translation = manifest.get("translation") or {}
    translation_path = Path(str(translation.get("display_text_path") or ""))
    units_path = Path(str(translation.get("units_path") or ""))
    units: list[dict[str, Any]] = []
    if translation.get("state") != "approved" or not translation.get("locked"):
        blocking.append("Утверждённый перевод ещё не прикреплён или не заблокирован.")
    elif not translation_path.is_file() or not units_path.is_file():
        blocking.append("Файлы утверждённого перевода отсутствуют.")
    else:
        expected_hash = str(translation.get("sha256") or "")
        try:
            actual_hash = _sha256_text(translation_path)
        except OSError as exc:
            blocking.append(f"Не удалось прочитать перевод: {exc}")
        else:
            if actual_hash != expected_hash:
                blocking.append(
                    "Утверждённый перевод изменился после блокировки; требуется повторное утверждение."
                )
        try:
            loaded_units = json.loads(units_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            blocking.append("Файл translation_units.json повреждён.")
        else:
            if not isinstance(loaded_units, list) or not loaded_units:
                blocking.append("В переводе не найдено ни одной редакционной единицы.")
            elif any(not isinstance(item, dict) for item in loaded_units):
                blocking.append("Файл редакционных единиц содержит запись неверного типа.")
            else:
                units = loaded_units
                ids = [str(item.get("id") or "") for item in units]
                if any(not value for value in ids) or len(ids) != len(set(ids)):
                    blocking.append("ID редакционных единиц пусты или повторяются.")
                if any(not str(item.get("display_text") or "").strip() for item in units):
                    blocking.append("Одна из редакционных единиц перевода пуста.")
                if any(not str(item.get("spoken_text") or "").strip() for item in units):
                    blocking.append("Одна из произносимых редакционных единиц пуста.")
                expected_units_hash = str(translation.get("units_sha256") or "")
                actual_units_hash = _canonical_json_sha256(units)
                if not expected_units_hash:
                    blocking.append("У редакционных единиц отсутствует fingerprint; переутвердите перевод.")
                elif actual_units_hash != expected_units_hash:
                    blocking.append(
                        "Редакционные единицы изменились после утверждения; требуется повторное утверждение."
                    )

    required_tools = [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]
    if required_tools:
        blocking.append("Не найдены системные инструменты: " + ", ".join(required_tools))

    source = manifest.get("source") or {}
    source_probe: dict[str, Any] = {}
    try:
        if source.get("kind") == "url":
            if importlib.util.find_spec("yt_dlp") is None:
                raise DubProjectError("Модуль yt-dlp не установлен в окружении бота.")
            source_probe = _probe_url(str(source.get("url") or ""))
        elif source.get("kind") == "telegram_file":
            local_path = Path(str(source.get("local_path") or ""))
            if not local_path.is_file():
                raise DubProjectError("Исходный Telegram-файл ещё не загружен локально.")
            source_probe = _probe_local_media(local_path)
        else:
            raise DubProjectError("Неизвестный тип исходника.")
    except (DubProjectError, subprocess.TimeoutExpired) as exc:
        blocking.append(str(exc))

    duration = float(source_probe.get("duration_seconds") or 0)
    profile, hardsub = (
        _profile_for_duration(duration)
        if duration > 0
        else ("pending_source_probe", False)
    )

    cpu_python = Path(
        os.getenv("VOXCPM2_CPU_PYTHON", str(DEFAULT_CPU_PYTHON))
    ).expanduser()
    archive_root = Path(
        os.getenv("VOXCPM2_ARCHIVE_ROOT", str(DEFAULT_ARCHIVE_ROOT))
    ).expanduser()
    runtime_report: dict[str, Any] | None = None
    if not cpu_python.is_file():
        blocking.append(f"Не найден CPU Python VoxCPM2: {cpu_python}")
    else:
        runtime_report, runtime_error = _probe_cpu_runtime(cpu_python)
        if runtime_error:
            blocking.append(runtime_error)

    model_snapshot = _find_model_snapshot(archive_root)
    if model_snapshot is None:
        blocking.append(f"Не найден локальный snapshot VoxCPM2 под: {archive_root}")

    engine = repository / "tools" / "voxcpm2" / "production" / "segmented_voice_clone.py"
    master = repository / "tools" / "voxcpm2" / "production" / "master_constant_mix.py"
    syntax_python = cpu_python if cpu_python.is_file() else Path(sys.executable)
    for label, path in (("production engine", engine), ("master mixer", master)):
        if not path.is_file():
            blocking.append(f"Отсутствует {label}: {path}")
            continue
        syntax_error = _compile_python(syntax_python, path)
        if syntax_error:
            blocking.append(f"Синтаксическая ошибка в {label}: {syntax_error}")

    source_bytes = int(source.get("bytes") or source.get("file_size") or 0)
    required_gib = _estimate_required_gib(duration, source_bytes)
    root.mkdir(parents=True, exist_ok=True)
    free_gib = shutil.disk_usage(root).free / (1024**3)
    if free_gib < required_gib:
        blocking.append(
            f"Недостаточно места: свободно {free_gib:.1f} ГБ, "
            f"нужно около {required_gib:.1f} ГБ."
        )

    if profile == "shorts_premium" and duration > SHORTS_MAX_SECONDS:
        warnings.append(
            "Длительность превышает 180 секунд только в пределах контейнерного допуска."
        )
    if profile == "long_premium":
        warnings.append(
            "Ролик длиннее трёх минут: hardsub отключён; SRT будет отдельным артефактом."
        )

    report: dict[str, Any] = {
        "schema_version": 2,
        "project_id": project_id,
        "checked_at": utc_now(),
        "ok": not blocking,
        "blocking_errors": blocking,
        "warnings": warnings,
        "profile": profile,
        "source": source_probe,
        "duration_seconds": round(duration, 6),
        "subtitles": {
            "hardsub": hardsub,
            "separate_srt": True,
            "translate_on_screen_text": False,
        },
        "translation": {
            "state": translation.get("state"),
            "locked": bool(translation.get("locked")),
            "sha256": translation.get("sha256"),
            "units_sha256": translation.get("units_sha256"),
            "contract_sha256": translation.get("contract_sha256"),
            "revision": translation.get("revision"),
            "unit_count": len(units),
            "rewrite_allowed": False,
            "auto_shorten_allowed": False,
        },
        "synthesis": {
            "engine": "VoxCPM2",
            "device": "cpu",
            "cpu_python": str(cpu_python),
            "runtime": runtime_report,
            "archive_root": str(archive_root),
            "model_snapshot": str(model_snapshot) if model_snapshot else None,
            "production_engine": str(engine),
            "master_mixer": str(master),
            "hidden_tts_fallback": False,
            "sidechain": False,
        },
        "disk": {
            "free_gib": round(free_gib, 3),
            "estimated_required_gib": round(required_gib, 3),
        },
    }
    record_preflight(project_id, report)
    return report
