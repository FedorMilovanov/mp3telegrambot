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
    """Метаданные YouTube в no-TUN требуют proxy/cookies И js-runtime для
    n-challenge. AUDIT R31b (in-process ВСЕГДА падал на YouTube «Only images»):
    берём тот же набор из YTDLP_BASE_ARGS через parse_options — точный паритет
    с рабочим subprocess, а не хардкод неполного набора."""
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:2600]
    assert "YTDLP_BASE_ARGS" in seg          # тот же источник флагов, что и subprocess
    assert "parse_options" in seg            # cookies+proxy+js-runtimes из флагов
    assert 'opts["noplaylist"] = True' in seg
    # прежний баг: жёсткий player_client=web без js-runtime — не должно вернуться
    assert "player_client" not in seg


def test_inprocess_returns_none_on_failure_for_fallback():
    seg = SRC.split("async def _ytdlp_info_inprocess(", 1)[1][:2500]
    assert "return None" in seg
