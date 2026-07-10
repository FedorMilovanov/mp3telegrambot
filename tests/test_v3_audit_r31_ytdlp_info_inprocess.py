#!/usr/bin/env python3
"""AUDIT R31 (живой баг: `python.exe Application Error 0xc0000142` +
`yt-dlp info error` на 10%): бот спавнит `sys.executable -m yt_dlp` для
получения метаданных, и дочерний python.exe падает на инициализации DLL
(0xc0000142) — при этом `python -m yt_dlp --version` из чистого терминала
работает. Причина — PATH процесса-бота (DLL-каталог, добавленный
лаунчером, перебивает системную DLL нового процесса).

Фикс: сначала берём метаданные через yt-dlp Python API В ПРОЦЕССЕ (без
спавна нового python.exe), и лишь при неудаче откатываемся на subprocess.
"""
from pathlib import Path

SRC = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")


def test_inprocess_info_helper_exists():
    assert "async def _ytdlp_info_inprocess(" in SRC
    # использует Python API, а не спавн
    assert "yt_dlp.YoutubeDL(opts)" in SRC
    assert "extract_info(url, download=False)" in SRC


def test_inprocess_tried_before_subprocess():
    """In-process попытка обязана идти ДО subprocess-ветки, а subprocess —
    остаться как fallback (поведение сохранено)."""
    inproc = SRC.find("info_dict = await _ytdlp_info_inprocess(url, _info_timeout)")
    subproc = SRC.find("subprocess.run(info_cmd")
    assert inproc != -1 and subproc != -1
    assert inproc < subproc, "in-process должен идти раньше subprocess-fallback"
    # subprocess вызывается только если in-process не дал результата
    assert "if info_dict is None:" in SRC


def test_inprocess_carries_cookies_and_proxy():
    """Метаданные YouTube в no-TUN режиме требуют proxy и часто cookies —
    in-process путь обязан их прокидывать (как pipelines/playlist.py)."""
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:2000]
    assert "_proxy_for_ytdlp()" in seg
    assert "cookiefile" in seg
    assert 'opts["noplaylist"] = True' in seg or '"noplaylist": True' in seg


def test_inprocess_returns_none_on_failure_for_fallback():
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:2500]
    assert "return None" in seg
