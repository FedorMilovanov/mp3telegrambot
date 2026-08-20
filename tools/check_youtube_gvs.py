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


def _configure_stdio() -> None:
    """Keep Russian diagnostics/help printable in Windows legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            pass


_configure_stdio()
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
    provider_failure = any(
        marker in folded
        for marker in (
            "_get_pot_via_script failed",
            "all 3 retries failed",
            "all 5 retries failed",
            "failed to generate po token",
            "po token provider rejected",
        )
    )
    if provider_failure and "503" in folded:
        return "FAIL_PO_PROVIDER_503"
    if provider_failure:
        return "FAIL_PO_PROVIDER"
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
        "--no-simulate",
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


def _print_egress_diagnostics() -> None:
    """Print the resolved yt-dlp proxy, cookies state and bgutil log path.

    These three facts resolve the GVS 403 audit: (1) which egress IP the media
    download uses, (2) whether the run is authenticated, (3) where bgutil logged
    ``Using proxy:`` — the only on-host proof of whether the PO token was minted
    from the same IP as the download.
    """
    from services.bgutil_http_runtime import bgutil_http_log_path
    from services.ffmpeg import COOKIES_FILE, _proxy_for_ytdlp

    proxy = _proxy_for_ytdlp()
    print(f"GVS egress: yt-dlp --proxy = {proxy or '(none — direct egress)'}")
    print(
        f"GVS auth: cookies.txt = "
        f"{'present' if COOKIES_FILE.exists() else 'MISSING (unauthenticated)'}"
    )
    log_path = bgutil_http_log_path()
    if log_path is not None:
        print(f"GVS bgutil log: {log_path} (grep 'Using proxy' after the run)")
    else:
        print("GVS bgutil log: silenced (BGUTIL_HTTP_LOG=none)")


def _http_403_hint() -> str:
    """Actionable, state-aware explanation for a GVS media 403.

    The token layer is proven good (see docs/AUDIT_YOUTUBE_GVS_403.md), so a 403
    here is an auth/egress outcome: YouTube refuses the media download because
    the request is unauthenticated and/or from a flagged (datacenter) IP. Tell
    the operator the two real fixes instead of leaving a bare 'FAIL'.
    """
    from services.ffmpeg import COOKIES_FILE, _proxy_for_ytdlp

    proxy = _proxy_for_ytdlp()
    has_cookies = COOKIES_FILE.exists()
    egress = proxy or "direct (residential)"
    lines = [
        "GVS_403_HINT: token is valid and IP-consistent; this is an auth/egress refusal.",
        "  egress = " + egress + ("  (datacenter-class IPs are routinely refused)" if proxy else "  (if still 403, the client/account is the issue)"),
    ]
    if not has_cookies:
        lines.append(
            "  FIX auth: export a logged-in YouTube session to cookies.txt in the project root"
            " (browser extension 'Get cookies.txt LOCALLY'). A Premium session bypasses GVS"
            " PO-token entirely. Then rerun this probe."
        )
    else:
        lines.append(
            "  auth: cookies.txt IS present — if still 403, the session is logged-out / non-Premium"
            " or this exit is hard-blocked; try a residential egress."
        )
    if proxy:
        lines.append(
            "  FIX egress: set YTDLP_PROXY_URL to a residential/clean proxy for YouTube only"
            " (it is independent of the Gemini/Telegram proxy), or run '--direct' to test residential."
        )
    return "\n".join(lines)


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
    parser.add_argument(
        "--direct",
        action="store_true",
        help=(
            "Принудительно пустить yt-dlp напрямую (residential), очистив proxy "
            "env vars в этом процессе. Перекрывает .env/system proxy — иначе "
            "TELEGRAM_PROXY_URL/YTDLP_PROXY_URL молча возвращают трафик в прокси "
            "и тест 'direct' оказывается недействительным."
        ),
    )
    args = parser.parse_args(argv)

    os.chdir(PROJECT_ROOT)
    if args.direct:
        # Must run before load_dotenv() in _prepare_runtime() and before
        # _build_ytdlp_base_args(). yt-dlp's request_proxy is derived from
        # self._downloader.proxies, which includes ambient HTTP(S)_PROXY — so
        # clearing these in-process is the only way to make a real direct run.
        for _key in (
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
            "http_proxy", "https_proxy", "all_proxy",
            "YTDLP_PROXY_URL", "TELEGRAM_PROXY_URL", "LOCAL_BOT_API_PROXY_URL",
        ):
            os.environ.pop(_key, None)
        print("GVS egress: --direct forced (proxy env cleared in-process)")
    try:
        runtime_status = _prepare_runtime()
    except Exception as exc:
        print(f"GVS_ACCEPTANCE=FAIL_RUNTIME\n{type(exc).__name__}: {exc}")
        return 2

    print(f"YouTube runtime: {runtime_status}")
    _print_egress_diagnostics()
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
            if classification == "FAIL_HTTP_403":
                print(_http_403_hint())
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
