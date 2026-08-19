#!/usr/bin/env python3
"""Manual end-to-end acceptance probe for YouTube maximum-quality GVS access.

This intentionally downloads the complete best-audio source with the same
production yt-dlp base arguments used by MP3Bot. It is a manual diagnostic,
not a startup health check: repeatedly probing YouTube on every bot launch
would add latency and unnecessary anti-bot pressure.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_PROBE_URL = "https://www.youtube.com/watch?v=-vq7fH7ANUs"
_EXPECTED_DURATION_PREFIX = "GVS_EXPECTED_DURATION="


def _tail(text: str, limit: int = 4000) -> str:
    value = str(text or "").strip()
    return value[-limit:]


def _classify_failure(process: subprocess.CompletedProcess[str]) -> str:
    detail = f"{process.stdout or ''}\n{process.stderr or ''}"
    folded = detail.casefold()
    if "http error 403" in folded or "403: forbidden" in folded:
        return "FAIL_HTTP_403"
    if "sign in to confirm you" in folded or "login_required" in folded:
        return "FAIL_LOGIN_REQUIRED"
    if "po token providers: none" in folded:
        return "FAIL_NO_PO_PROVIDER"
    return "FAIL_YTDLP"


def _expected_duration_from_output(stdout: str) -> float:
    for raw_line in reversed(str(stdout or "").splitlines()):
        line = raw_line.strip()
        if not line.startswith(_EXPECTED_DURATION_PREFIX):
            continue
        raw_value = line[len(_EXPECTED_DURATION_PREFIX) :].strip()
        try:
            value = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return value if value > 0 else 0.0
    return 0.0


def _duration_matches(actual: float, expected: float) -> bool:
    """Match the Factory complete-source tolerance exactly."""
    try:
        actual_value = float(actual)
        expected_value = float(expected)
    except (TypeError, ValueError, OverflowError):
        return False
    if actual_value <= 0 or expected_value <= 0:
        return False
    tolerance = max(2.0, min(15.0, expected_value * 0.002))
    return abs(actual_value - expected_value) <= tolerance


def _find_downloaded_media(workdir: Path) -> Path | None:
    candidates = [
        path
        for path in workdir.iterdir()
        if path.is_file()
        and not path.name.endswith((".part", ".ytdl", ".temp"))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_size)


def _ffprobe_duration(media_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe не найден в PATH")
    try:
        process = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(media_path),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffprobe не завершился штатно: {exc}") from exc
    if process.returncode:
        raise RuntimeError(
            "ffprobe не смог прочитать скачанный файл: "
            + (_tail(process.stderr) or _tail(process.stdout) or f"rc={process.returncode}")
        )
    try:
        duration = float((process.stdout or "").strip())
    except ValueError as exc:
        raise RuntimeError("ffprobe вернул некорректную duration") from exc
    if duration <= 0:
        raise RuntimeError(f"ffprobe duration <= 0: {duration}")
    return duration


def _production_command(url: str, workdir: Path) -> list[str]:
    # Import only after cwd/env setup in main so repo-relative yt-dlp policy and
    # cookies resolve exactly as they do for bot_new.py.
    from services.ffmpeg import _build_ytdlp_base_args

    output_template = workdir / "gvs_probe.%(ext)s"
    return [
        *_build_ytdlp_base_args(),
        "--abort-on-unavailable-fragments",
        "--format",
        "bestaudio/best",
        "--no-playlist",
        "--print",
        f"before_dl:{_EXPECTED_DURATION_PREFIX}%(duration)s",
        "--output",
        str(output_template),
        url,
    ]


async def _run_download_async(
    url: str,
    workdir: Path,
) -> subprocess.CompletedProcess[str]:
    from services.async_process import run_cancellable_process

    return await run_cancellable_process(
        _production_command(url, workdir),
        cwd=PROJECT_ROOT,
        timeout=1800,
        text=True,
    )


def _run_download(url: str, workdir: Path) -> subprocess.CompletedProcess[str]:
    # This tool is a synchronous CLI. The repo-owned async runner isolates the
    # child process group and tears down yt-dlp + Node descendants on timeout.
    return asyncio.run(_run_download_async(url, workdir))


def _prepare_runtime() -> str:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    from services.youtube_po_token_runtime import require_youtube_po_token_runtime

    return require_youtube_po_token_runtime().status_text()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Полностью скачать bestaudio/best через production yt-dlp route и "
            "проверить, что GVS media читается до конца."
        )
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=DEFAULT_PROBE_URL,
        help="Public YouTube URL; default is the original regression video.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Не удалять скачанный probe-файл после проверки.",
    )
    args = parser.parse_args(argv)

    os.chdir(PROJECT_ROOT)
    try:
        runtime_status = _prepare_runtime()
    except Exception as exc:
        print(f"GVS_ACCEPTANCE=FAIL_RUNTIME\n{type(exc).__name__}: {exc}")
        return 2

    print(f"YouTube runtime: {runtime_status}")
    temp_root = Path(tempfile.mkdtemp(prefix="mp3bot-youtube-gvs-"))
    try:
        print("GVS probe: downloading complete bestaudio/best with production policy...")
        try:
            process = _run_download(args.url, temp_root)
        except subprocess.TimeoutExpired as exc:
            print(f"GVS_ACCEPTANCE=FAIL_TIMEOUT\n{exc}")
            return 6
        except OSError as exc:
            print(f"GVS_ACCEPTANCE=FAIL_YTDLP\n{exc}")
            return 3
        except RuntimeError as exc:
            print(f"GVS_ACCEPTANCE=FAIL_PROCESS_OWNERSHIP\n{exc}")
            return 8

        if process.returncode:
            classification = _classify_failure(process)
            detail = _tail(process.stderr) or _tail(process.stdout)
            print(f"GVS_ACCEPTANCE={classification}")
            if detail:
                print(detail)
            return 3

        expected_duration = _expected_duration_from_output(process.stdout)
        if expected_duration <= 0:
            print("GVS_ACCEPTANCE=FAIL_METADATA_DURATION")
            return 7

        media_path = _find_downloaded_media(temp_root)
        if media_path is None or media_path.stat().st_size <= 0:
            print("GVS_ACCEPTANCE=FAIL_NO_MEDIA")
            return 4

        try:
            duration = _ffprobe_duration(media_path)
        except RuntimeError as exc:
            print(f"GVS_ACCEPTANCE=FAIL_FFPROBE\n{exc}")
            return 5

        if not _duration_matches(duration, expected_duration):
            print(
                "GVS_ACCEPTANCE=FAIL_DURATION_MISMATCH "
                f"metadata={expected_duration:.3f}s ffprobe={duration:.3f}s"
            )
            return 9

        print(
            "GVS_ACCEPTANCE=PASS "
            f"bytes={media_path.stat().st_size} "
            f"metadata={expected_duration:.3f}s ffprobe={duration:.3f}s"
        )
        if args.keep:
            print(f"Probe file kept at: {media_path}")
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
