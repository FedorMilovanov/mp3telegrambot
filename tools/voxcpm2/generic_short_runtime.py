#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Production runtime adapters for the generic Shorts dubbing pipeline.

Keeps the core pipeline deterministic while reusing the bot's hardened yt-dlp
configuration and Gemini proxy/high-thinking policy. Translation requests use a
request-local key pool with explicit network budgets, so one dead route cannot
make the Dub worker look frozen for fifteen minutes.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.text_utils import title_case_fragment
import tools.voxcpm2.generic_short_production as pipeline

_TITLE_PROMPT_MARKER = "Ты создаёшь имя готового русского видеофайла"
_JOHN_PIPER_RE = re.compile(
    r"\b(?:john\s+piper|джон\s+пайпер)\b",
    re.IGNORECASE,
)
_GEMINI_KEY_NAMES = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
)
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0
_DEFAULT_PASS_TIMEOUT_SECONDS = 300.0
_MIN_REQUEST_TIMEOUT_SECONDS = 30.0


def standardize_russian_title(value: str, *, context: str = "") -> str:
    """Apply the same title casing already used by Shorts and Clips."""
    title = re.sub(r"\s+", " ", str(value or "")).strip(" .—–-")
    title = re.sub(
        r"\bJohn\s+Piper\b",
        "Джон Пайпер",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\bхристианской\s+женщины\b",
        "женщины христианки",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s+[—–-]\s+", " - ", title)

    combined = f"{context}\n{title}"
    if _JOHN_PIPER_RE.search(combined):
        title = _JOHN_PIPER_RE.sub("Джон Пайпер", title)
        title = re.sub(
            r"(?:\s+-\s+)?Джон\s+Пайпер\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip(" .—–-")
        title = f"{title} - Джон Пайпер" if title else "Джон Пайпер"

    return title_case_fragment(re.sub(r"\s+", " ", title).strip())


def _standardize_title_payload(payload: Any, prompt: str) -> Any:
    if _TITLE_PROMPT_MARKER not in prompt or not isinstance(payload, dict):
        return payload
    result = dict(payload)
    result["title"] = standardize_russian_title(
        str(result.get("title") or ""),
        context=prompt,
    )
    return result


def _install_project_title_standard() -> None:
    for module in list(sys.modules.values()):
        if module is None:
            continue
        file_name = Path(
            str(getattr(module, "__file__", "") or "")
        ).name.casefold()
        if file_name != "generic_project_runtime.py":
            continue
        original = getattr(module, "generate_russian_title", None)
        if (
            not callable(original)
            or getattr(original, "_dub_title_standard", False)
        ):
            continue

        def standardized_generate(
            *args: Any,
            _original: Any = original,
            **kwargs: Any,
        ) -> str:
            generated = str(_original(*args, **kwargs) or "")
            metadata = args[0] if args else kwargs.get("metadata", {})
            try:
                context = json.dumps(metadata, ensure_ascii=False)
            except (TypeError, ValueError):
                context = str(metadata or "")
            return standardize_russian_title(generated, context=context)

        standardized_generate._dub_title_standard = True  # type: ignore[attr-defined]
        module.generate_russian_title = standardized_generate


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
        [
            *base,
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            url,
        ],
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
    files = sorted(
        source_dir.glob("captions*.vtt"),
        key=lambda path: path.stat().st_size,
        reverse=True,
    )
    for path in files:
        cues = pipeline.parse_vtt(path)
        if cues:
            pipeline.log(f"Использую YouTube captions: {path.name}")
            return cues
    if proc.returncode != 0:
        pipeline.log(
            "YouTube captions недоступны; включаю Whisper fallback."
        )
    return []


def _bounded_env_seconds(
    name: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None or not raw.strip() else float(raw.strip())
    except (TypeError, ValueError, OverflowError):
        value = default
    if not value == value or value in {float("inf"), float("-inf")}:
        value = default
    return max(minimum, min(value, maximum))


def _translation_timeouts() -> tuple[float, float]:
    request_timeout = _bounded_env_seconds(
        "DUB_GEMINI_REQUEST_TIMEOUT_SEC",
        default=_DEFAULT_REQUEST_TIMEOUT_SECONDS,
        minimum=_MIN_REQUEST_TIMEOUT_SECONDS,
        maximum=600.0,
    )
    pass_timeout = _bounded_env_seconds(
        "DUB_GEMINI_PASS_TIMEOUT_SEC",
        default=_DEFAULT_PASS_TIMEOUT_SECONDS,
        minimum=60.0,
        maximum=1200.0,
    )
    return request_timeout, max(request_timeout, pass_timeout)


def _load_dotenv_for_manual_run() -> None:
    """Load project keys for direct CLI use without overriding worker env."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(override=False)
    except Exception:
        return


def _translation_keys() -> list[str]:
    _load_dotenv_for_manual_run()
    values: list[str] = []
    for name in _GEMINI_KEY_NAMES:
        key = os.getenv(name, "").strip()
        if key and key not in values:
            values.append(key)
    return values


def _translation_client(api_key: str, timeout_ms: int) -> Any:
    """Create one request-local client while retaining the environment proxy."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError(
            "Пакет google-genai не установлен в окружении бота."
        ) from exc
    options = types.HttpOptions(timeout=max(30_000, int(timeout_ms)))
    return genai.Client(api_key=api_key, http_options=options)


def _close_client(client: Any) -> None:
    closer = getattr(client, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _prompt_label(prompt: str) -> str:
    lowered = str(prompt or "").casefold()
    if _TITLE_PROMPT_MARKER.casefold() in lowered:
        return "русское название"
    if "первоклассный переводчик" in lowered:
        return "черновой перевод"
    if "старший двуязычный редактор" in lowered:
        return "сверка смысла"
    if "режиссёр русской речевой записи" in lowered:
        return "финальная речевая редактура"
    if "редактор произносимости" in lowered:
        return "сжатие перегруженных реплик"
    return "Gemini JSON"


def _generation_config(model_name: str) -> Any:
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
    if config is not None:
        return config
    try:
        from google.genai import types

        return types.GenerateContentConfig(
            response_mime_type="application/json",
            max_output_tokens=16000,
            thinking_config=types.ThinkingConfig(
                thinking_level="high",
            ),
        )
    except Exception as exc:
        raise RuntimeError(
            "Не удалось создать Gemini high-thinking config для перевода."
        ) from exc


def gemini_json(prompt: str, *, model_name: str) -> Any:
    keys = _translation_keys()
    if not keys:
        raise RuntimeError(
            "Для редакторского перевода нужен GEMINI_API_KEY в .env."
        )

    request_timeout, pass_timeout = _translation_timeouts()
    deadline = time.monotonic() + pass_timeout
    config = _generation_config(model_name)
    label = _prompt_label(prompt)
    errors: list[str] = []
    pipeline.log(
        f"Gemini: начинаю «{label}»; ключей={len(keys)}; "
        f"лимит ключа={request_timeout:.0f} сек.; прохода={pass_timeout:.0f} сек."
    )

    for index, api_key in enumerate(keys, start=1):
        remaining = deadline - time.monotonic()
        if remaining < _MIN_REQUEST_TIMEOUT_SECONDS:
            errors.append(
                f"остаток общего лимита {max(0.0, remaining):.1f} сек. меньше "
                f"минимального запроса {_MIN_REQUEST_TIMEOUT_SECONDS:.0f} сек."
            )
            break
        effective_timeout = min(request_timeout, remaining)
        timeout_ms = int(round(effective_timeout * 1000.0))
        client = _translation_client(api_key, timeout_ms)
        started = time.monotonic()
        pipeline.log(
            f"Gemini: «{label}», ключ {index}/{len(keys)}, "
            f"таймаут {timeout_ms / 1000:.0f} сек."
        )
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            payload = pipeline._extract_json(
                getattr(response, "text", "")
            )
            elapsed = time.monotonic() - started
            pipeline.log(
                f"Gemini: «{label}» завершён ключом {index}/{len(keys)} "
                f"за {elapsed:.1f} сек."
            )
            return _standardize_title_payload(payload, prompt)
        except Exception as exc:
            elapsed = time.monotonic() - started
            errors.append(
                f"key#{index} {elapsed:.1f}s: {type(exc).__name__}: "
                f"{str(exc)[:220]}"
            )
            pipeline.log(
                f"Gemini: «{label}», ключ {index}/{len(keys)} не сработал "
                f"за {elapsed:.1f} сек.; пробую следующий."
            )
        finally:
            _close_client(client)

    raise RuntimeError(
        f"Gemini не завершил «{label}» в пределах {pass_timeout:.0f} сек.: "
        + " | ".join(errors)
    )


def install_runtime_adapters() -> None:
    pipeline.download_source = download_source
    pipeline.download_captions = download_captions
    pipeline.gemini_json = gemini_json
    _install_project_title_standard()

    # The legacy entrypoint still installs its old TTS guard. Clean production
    # never calls this function; it imports only download/Gemini functions above.
    from tools.voxcpm2.semantic_tts_guard import (
        install as install_semantic_tts_guard,
    )

    install_semantic_tts_guard()


def main() -> None:
    install_runtime_adapters()
    pipeline.main()


if __name__ == "__main__":
    main()
