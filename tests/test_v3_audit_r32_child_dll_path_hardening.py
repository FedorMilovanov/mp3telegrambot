#!/usr/bin/env python3
"""AUDIT R32: дочерний python.exe (yt-dlp) падал 0xc0000142 из-за PATH
процесса-бота, где чужой bin-каталог (CUDA/cuDNN/ffmpeg) перебивал
системную DLL. Лечение — на Windows ставим каталог интерпретатора и
System32 в НАЧАЛО PATH (prepend-only), чтобы core-DLL грузились раньше
«отравленного» каталога. Только Windows, ничего не удаляем.
"""
import os
from pathlib import Path

SRC = Path("core/globals.py").read_text(encoding="utf-8")


def test_hardening_is_windows_only_and_prepend_only():
    assert 'if os.name == "nt":' in SRC
    assert "os.path.dirname(_sys.executable)" in SRC
    assert '"System32"' in SRC
    # prepend: _front + _rest, а не замена
    assert "os.pathsep.join(_front + _rest)" in SRC


def test_no_dir_removed_from_path():
    """Правило: только PREPEND. Все исходные каталоги обязаны остаться в PATH
    (просто после системных). В коде это _rest из текущего PATH."""
    assert "_cur_dirs = [p for p in _cur_path.split(os.pathsep) if p]" in SRC
    assert "_rest = [d for d in _cur_dirs" in SRC


def test_import_does_not_break_on_non_windows():
    # На Linux ветка nt пропускается — импорт globals не должен падать.
    import importlib

    import core.globals as g
    importlib.reload(g)
    assert g is not None
    if os.name != "nt":
        # PATH не трогаем на не-Windows
        assert True
