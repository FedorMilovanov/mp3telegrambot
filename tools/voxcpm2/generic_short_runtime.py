#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production runtime adapters for the generic Shorts dubbing pipeline.

Keeps the core pipeline deterministic while reusing the bot's hardened yt-dlp
configuration and Gemini client pool/high-thinking policy. Also installs the
semantic VoxCPM2 guard shared by Gemini MAX and ready-SRT modes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import tools.voxcpm2.generic_short_production as pipeline


def _ytdlp_base() -> list[str]:
    try:
        from services.ffmpeg import YTDLP_BASE_ARGS

        return [str(part) for part in YTDLP_BASE_ARGS]
    except Exception:
        return [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-config",
            "--sleep-interval",
            "2",
            "--concurrent-fragments",
            "4",
            "--format-sort",
            "ext:mp4:m4a",
        ]


def download_source(url: str, source: Path) -> dict[str, Any]:
    source.parent.mkdir(parents=True, exist_ok=True)
    base = _ytdlp_base()
    metadata_proc = pipeline.run_checked(
        [*base, "--dump-single-json", "--skip-download", "--no-playlist", url],
        capture=True,
        timeout=300,
    )
    metadata = json.loads(metadata_proc.stdout or "{}")
    if not source.is_file() or source.stat().st_size < 100_000:
        pipeline.run_checked(
            [
                *base,
                "--no-playlist",
                "--windows-filenames",
                "-f",
                "bv*+ba/b",
                "--merge-output-format",
                "mp4",
                "-o",
                str(source),
                url,
            ],
            timeout=1800,
        )
    return metadata


def download_captions(url: str, source_dir: Path) -> list[pipeline.Cue]:
    template = source_dir / "captions.%(language)s.%(ext)s"
    proc = subprocess.run(
        [
            *_ytdlp_base(),
            "--no-playlist",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en.*,en",
            "--sub-format",
            "vtt",
            "-o",
            str(template),
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=420,
        check=False,
    )
    files = sorted(source_dir.glob("captions*.vtt"), key=lambda path: path.stat().st_size, reverse=True)
    for path in files:
        cues = pipeline.parse_vtt(path)
        if cues:
            pipeline.log(f"Использую YouTube captions: {path.name}")
            return cues
    if proc.returncode != 0:
        pipeline.log("YouTube captions недоступны; включаю Whisper fallback.")
    return []


def _fallback_clients() -> list[Any]:
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("Пакет google-genai не установлен в окружении бота.") from exc
    result: list[Any] = []
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"):
        key = os.getenv(name, "").strip()
        if key:
            result.append(genai.Client(api_key=key))
    return result


def gemini_json(prompt: str, *, model_name: str) -> Any:
    clients: list[Any] = []
    try:
        from core.globals import GEMINI_CLIENTS

        clients = list(GEMINI_CLIENTS or [])
    except Exception:
        clients = []
    if not clients:
        clients = _fallback_clients()
    if not clients:
        raise RuntimeError("Для редакторского перевода нужен GEMINI_API_KEY в .env.")

    config = None
    try:
        from core.globals import make_text_config_smart

        config = make_text_config_smart(
            max_output_tokens=16000,
            model_name=model_name,
            thinking_level="high",
            response_mime_type="application/json",
        )
    except Exception:
        config = None
    if config is None:
        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=16000,
            )
        except Exception as exc:
            raise RuntimeError("Не удалось создать Gemini config для перевода.") from exc

    errors: list[str] = []
    for index, client in enumerate(clients, start=1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            return pipeline._extract_json(getattr(response, "text", ""))
        except Exception as exc:
            errors.append(f"key#{index}: {type(exc).__name__}: {str(exc)[:220]}")
            pipeline.log(f"Gemini translation route key#{index} не сработал; пробую следующий ключ.")
    raise RuntimeError("Все Gemini-ключи завершились ошибкой: " + " | ".join(errors))


def install_runtime_adapters() -> None:
    pipeline.download_source = download_source
    pipeline.download_captions = download_captions
    pipeline.gemini_json = gemini_json

    # Install after project/direct modules have imported their normal subprocess
    # reference. The guard patches only the VoxCPM2 synthesis command and leaves
    # source download, FFmpeg mastering and all mode routing untouched.
    from tools.voxcpm2.semantic_tts_guard import install as install_semantic_tts_guard

    install_semantic_tts_guard()


def main() -> None:
    install_runtime_adapters()
    pipeline.main()


if __name__ == "__main__":
    main()
