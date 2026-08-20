#!/usr/bin/env python3
"""Fail-closed readiness checks for browserless YouTube PO-token routing."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from services.bgutil_http_runtime import (
    BgutilHttpRuntimeError,
    DEFAULT_BASE_URL as BGUTIL_HTTP_BASE_URL,
    ensure_bgutil_http_runtime,
)

# Kept as an explicit migration identity; production no longer imports this
# distribution. yt-dlp is restricted to the exact source plugin directory.
BGUTIL_DISTRIBUTION = "bgutil-ytdlp-pot-provider"
LEGACY_WPC_DISTRIBUTION = "yt-dlp-getpot-wpc"
BGUTIL_MODULE = "yt_dlp_plugins.extractor.getpot_bgutil"
BGUTIL_EXPECTED_VERSION = "1.3.1"
BGUTIL_EXPECTED_COMMIT = "a0be2352807e3bd6991f09d2cab685a0ab825b26"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER_ROOT = PROJECT_ROOT / ".runtime" / "bgutil-ytdlp-pot-provider"
DEFAULT_PROVIDER_HOME = DEFAULT_PROVIDER_ROOT / "server"
YTDLP_POLICY_FILE = PROJECT_ROOT / "yt-dlp.conf"
EXPECTED_PLUGIN_DIR = ".runtime/bgutil-ytdlp-pot-provider"
EXPECTED_YOUTUBE_ROUTE = "youtube:player_client=mweb"
EXPECTED_BGUTIL_ROUTE = f"youtubepot-bgutilhttp:base_url={BGUTIL_HTTP_BASE_URL}"
_PROVIDER_PROBE_TIMEOUT_SEC = 20
_PROVIDER_PROBE_CODE = """\
import importlib
import pathlib
import sys

plugin_root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(plugin_root))
module = importlib.import_module(sys.argv[2])
module_version = str(getattr(module, "__version__", "") or "").strip()
module_file = pathlib.Path(getattr(module, "__file__", "") or "").resolve()
print(module_version)
print(module_file)
try:
    module_file.relative_to(plugin_root)
except ValueError:
    raise SystemExit(4)
raise SystemExit(0 if not module_version or module_version == sys.argv[3] else 3)
"""


class YouTubePoTokenRuntimeError(RuntimeError):
    """Raised when the required automatic YouTube PO-token path is unavailable."""


@dataclass(frozen=True)
class YouTubePoTokenRuntime:
    provider_version: str
    provider_commit: str
    node_version: str
    provider_home: Path
    plugin_root: Path
    http_base_url: str

    def status_text(self) -> str:
        return (
            f"bgutil {self.provider_version}@{self.provider_commit[:8]}; "
            f"node={self.node_version}; http={self.http_base_url}; "
            "browserless=on; source-only=on"
        )


def _require_no_legacy_browser_provider() -> None:
    try:
        legacy_version = metadata.version(LEGACY_WPC_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return
    raise YouTubePoTokenRuntimeError(
        "Обнаружен устаревший browser-based PO Token provider "
        f"{LEGACY_WPC_DISTRIBUTION} {legacy_version}. Удали его из project venv: "
        ".\\.venv\\Scripts\\python.exe -m pip uninstall -y "
        "yt-dlp-getpot-wpc nodriver; затем снова запусти Start Bot.bat. "
        "Chrome fallback в production запрещён."
    )


def _require_no_installed_bgutil_wheel() -> None:
    try:
        wheel_version = metadata.version(BGUTIL_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return
    raise YouTubePoTokenRuntimeError(
        "Обнаружен лишний установленный bgutil provider wheel "
        f"{BGUTIL_DISTRIBUTION} {wheel_version}. Production использует только "
        "pinned .runtime source tree. Удали wheel: "
        ".\\.venv\\Scripts\\python.exe -m pip uninstall -y "
        "bgutil-ytdlp-pot-provider; затем снова запусти Start Bot.bat."
    )


def _parse_ytdlp_policy(path: Path) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="strict")
    except OSError as exc:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf отсутствует или не читается; exact-source PO Token policy "
            "не может быть доказана"
        ) from exc
    try:
        return shlex.split(text, comments=True, posix=True)
    except ValueError as exc:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf синтаксически повреждён; exact-source PO Token policy "
            "не может быть доказана"
        ) from exc


def _option_values(tokens: list[str], option: str) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == option:
            if index + 1 >= len(tokens):
                raise YouTubePoTokenRuntimeError(
                    f"yt-dlp.conf: {option} задан без значения"
                )
            values.append(tokens[index + 1])
            index += 2
            continue
        prefix = option + "="
        if token.startswith(prefix):
            values.append(token[len(prefix):])
        index += 1
    return values


def _require_ytdlp_policy(path: Path = YTDLP_POLICY_FILE) -> None:
    """Prove that yt-dlp cannot fall back to global plugins or another client."""
    tokens = _parse_ytdlp_policy(path)
    if tokens.count("--no-plugin-dirs") != 1:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf должен ровно один раз отключать default/global plugin "
            "directories через --no-plugin-dirs"
        )
    disable_index = tokens.index("--no-plugin-dirs")

    plugin_values = _option_values(tokens, "--plugin-dirs")
    if plugin_values != [EXPECTED_PLUGIN_DIR]:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf должен разрешать ровно один pinned plugin root: "
            f"{EXPECTED_PLUGIN_DIR}; actual={plugin_values or 'none'}"
        )
    plugin_token_indexes = [
        idx
        for idx, token in enumerate(tokens)
        if token == "--plugin-dirs" or token.startswith("--plugin-dirs=")
    ]
    if not plugin_token_indexes or disable_index > plugin_token_indexes[0]:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf должен сначала отключить global plugins через "
            "--no-plugin-dirs, затем разрешить pinned source root"
        )

    extractor_args = _option_values(tokens, "--extractor-args")
    youtube_routes = [
        value for value in extractor_args if value.startswith("youtube:player_client=")
    ]
    bgutil_routes = [
        value
        for value in extractor_args
        if value.startswith("youtubepot-bgutilhttp:base_url=")
    ]
    script_routes = [
        value for value in extractor_args if value.startswith("youtubepot-bgutilscript:")
    ]
    if youtube_routes != [EXPECTED_YOUTUBE_ROUTE]:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf должен использовать ровно youtube:player_client=mweb; "
            f"actual={youtube_routes or 'none'}"
        )
    if bgutil_routes != [EXPECTED_BGUTIL_ROUTE]:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf должен направлять bgutil HTTP provider только в локальный "
            f"pinned runtime {BGUTIL_HTTP_BASE_URL}; actual={bgutil_routes or 'none'}"
        )
    if script_routes:
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf не должен конфигурировать bgutil script provider: "
            "production владеет persistent HTTP provider"
        )

    forbidden = ("--cookies", "--cookies-from-browser")
    if any(
        token in forbidden
        or any(token.startswith(option + "=") for option in forbidden)
        for token in tokens
    ):
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf не должен владеть cookies; cookie policy принадлежит "
            "services.ffmpeg"
        )
    if any("po_token=" in token.casefold() for token in tokens):
        raise YouTubePoTokenRuntimeError(
            "yt-dlp.conf содержит manual po_token; production требует "
            "автоматический video-bound provider"
        )


def _probe_tail(process: object, limit: int = 900) -> str:
    stderr = str(getattr(process, "stderr", "") or "").strip()
    stdout = str(getattr(process, "stdout", "") or "").strip()
    detail = stderr or stdout
    return detail[-limit:]


def _require_bgutil_module(plugin_root: Path, expected_version: str) -> str:
    """Import only the pinned source plugin in an isolated child interpreter."""
    plugin_root = Path(plugin_root).resolve()
    command = [
        sys.executable,
        "-c",
        _PROVIDER_PROBE_CODE,
        str(plugin_root),
        BGUTIL_MODULE,
        expected_version,
    ]
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROVIDER_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError(
            "exact-source bgutil plugin не проходит изолированную проверку "
            "совместимости. Перезапусти Start Bot.bat."
        ) from exc

    stdout_lines = [
        line.strip()
        for line in str(getattr(process, "stdout", "") or "").splitlines()
        if line.strip()
    ]
    actual_version = stdout_lines[0] if stdout_lines else ""
    if process.returncode == 0:
        return actual_version or expected_version

    detail = _probe_tail(process)
    if process.returncode == 3:
        raise YouTubePoTokenRuntimeError(
            "exact-source bgutil plugin имеет рассинхронизированную версию: "
            f"expected={expected_version} module={actual_version or 'unknown'}"
        )
    if process.returncode == 4:
        raise YouTubePoTokenRuntimeError(
            "bgutil plugin импортирован не из pinned .runtime source tree; "
            "глобальный/site-packages provider запрещён"
        )

    suffix = f" Детали: {detail}" if detail else ""
    raise YouTubePoTokenRuntimeError(
        "exact-source bgutil plugin не импортируется вместе с текущим yt-dlp. "
        f"Перезапусти Start Bot.bat.{suffix}"
    )


def _require_provider_build() -> Path:
    """Validate exactly the provider tree referenced by production yt-dlp.conf."""
    home = DEFAULT_PROVIDER_HOME.resolve()
    provider_root = home.parent
    generate_script = home / "build" / "generate_once.js"
    http_script = home / "build" / "main.js"
    plugin_entry = (
        provider_root / "plugin" / "yt_dlp_plugins" / "extractor" / "getpot_bgutil.py"
    )
    if not generate_script.is_file() or not http_script.is_file() or not plugin_entry.is_file():
        raise YouTubePoTokenRuntimeError(
            "browserless bgutil exact-source runtime не собран полностью "
            "(нужны build/main.js, build/generate_once.js и plugin). "
            "Запусти 'Start Bot.bat' для безопасной пересборки runtime."
        )
    marker = provider_root / ".mp3bot-bgutil-version"
    try:
        marker_value = marker.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise YouTubePoTokenRuntimeError(
            "browserless bgutil runtime не имеет commit marker; "
            "перезапусти Start Bot.bat для безопасной пересборки"
        ) from exc
    expected_marker = f"{BGUTIL_EXPECTED_VERSION}@{BGUTIL_EXPECTED_COMMIT}"
    if marker_value != expected_marker:
        raise YouTubePoTokenRuntimeError(
            "browserless bgutil runtime не соответствует pinned commit: "
            f"expected={expected_marker} actual={marker_value or 'empty'}"
        )
    return home


def _require_node() -> str:
    node = shutil.which("node")
    if not node:
        raise YouTubePoTokenRuntimeError(
            "Node.js не найден; exact-source bgutil требует Node.js >=22"
        )
    try:
        process = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise YouTubePoTokenRuntimeError("Не удалось запустить Node.js") from exc
    version = (process.stdout or process.stderr or "").strip().lstrip("v")
    match = re.match(r"(\d+)", version)
    if process.returncode or not match:
        raise YouTubePoTokenRuntimeError("Не удалось определить версию Node.js")
    if int(match.group(1)) < 22:
        raise YouTubePoTokenRuntimeError(
            f"Node.js {version} < 22; обнови Node.js"
        )
    return version


def require_youtube_po_token_runtime() -> YouTubePoTokenRuntime:
    """Require mweb + a live automatic browserless GVS provider, fail closed."""
    _require_no_legacy_browser_provider()
    _require_no_installed_bgutil_wheel()
    _require_ytdlp_policy()
    provider_home = _require_provider_build()
    plugin_root = provider_home.parent / "plugin"
    provider_version = _require_bgutil_module(plugin_root, BGUTIL_EXPECTED_VERSION)
    node_version = _require_node()
    node_executable = shutil.which("node")
    if not node_executable:
        raise YouTubePoTokenRuntimeError("Node.js исчез после startup-проверки")
    try:
        http_base_url = ensure_bgutil_http_runtime(
            node_executable=node_executable,
            server_home=provider_home,
            expected_version=BGUTIL_EXPECTED_VERSION,
            base_url=BGUTIL_HTTP_BASE_URL,
        )
    except BgutilHttpRuntimeError as exc:
        raise YouTubePoTokenRuntimeError(
            f"persistent bgutil HTTP provider не готов: {exc}"
        ) from exc
    return YouTubePoTokenRuntime(
        provider_version=provider_version,
        provider_commit=BGUTIL_EXPECTED_COMMIT,
        node_version=node_version,
        provider_home=provider_home,
        plugin_root=plugin_root,
        http_base_url=http_base_url,
    )


__all__ = [
    "BGUTIL_DISTRIBUTION",
    "BGUTIL_EXPECTED_VERSION",
    "BGUTIL_EXPECTED_COMMIT",
    "BGUTIL_HTTP_BASE_URL",
    "BGUTIL_MODULE",
    "DEFAULT_PROVIDER_HOME",
    "DEFAULT_PROVIDER_ROOT",
    "EXPECTED_BGUTIL_ROUTE",
    "EXPECTED_PLUGIN_DIR",
    "EXPECTED_YOUTUBE_ROUTE",
    "LEGACY_WPC_DISTRIBUTION",
    "YTDLP_POLICY_FILE",
    "YouTubePoTokenRuntime",
    "YouTubePoTokenRuntimeError",
    "require_youtube_po_token_runtime",
]
