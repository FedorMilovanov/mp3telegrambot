from __future__ import annotations

import ast
from pathlib import Path


def _process_function_source() -> tuple[str, str]:
    path = Path("pipelines/main_pipeline.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef)
        and item.name == "process_single_video"
    )
    return source, ast.get_source_segment(source, node) or ""


def _audio_stage_source() -> str:
    return Path("pipelines/audio_stage.py").read_text(encoding="utf-8")


def test_main_pipeline_external_commands_use_async_process_owner() -> None:
    """Каждый внешний процесс идёт через owned-раннер, а не через subprocess.

    Часть команд (yt-dlp для mp3) переехала в `pipelines/audio_stage.py`;
    контракт проверяется там, где команды теперь собираются, плюс в пайплайне
    остаётся запрет на прямой `subprocess.run`.
    """
    source, function = _process_function_source()

    assert "from services.async_process import run_cancellable_process" in source
    assert "subprocess.run(" not in function
    stage = _audio_stage_source()
    assert "from services.async_process import run_cancellable_process" in stage
    assert "await run_cancellable_process(" in stage
    # Ни в одном из двух файлов нет «голого» спавна процесса.
    assert "subprocess.run(" not in stage


def test_main_pipeline_thread_normalizer_remains_owned() -> None:
    """mp3gain идёт в потоке, но под owned-обёрткой, а не «сырым» to_thread."""
    stage = _audio_stage_source()

    assert "from services.async_worker import await_owned_coroutine" in stage
    assert "await await_owned_coroutine(asyncio.to_thread(normalize_mp3_lossless" in stage
    assert stage.count("asyncio.to_thread(normalize_mp3_lossless") == 1


def test_recompression_uses_atomic_mp3_conversion_owner() -> None:
    """Пережатие — только через проверенную атомарную конверсию, без ffmpeg напрямую."""
    stage = _audio_stage_source()

    assert "from services.mp3_conversion import reencode_mp3_64k_atomic" in stage
    assert "await reencode_mp3_64k_atomic(path, target)" in stage
    assert "path.unlink(missing_ok=True)" in stage
    assert "_recompress_proc" not in stage
    assert "ffmpeg -i" not in stage


def test_cached_audio_download_checks_exit_status() -> None:
    """Ненулевой rc yt-dlp обязан стать ошибкой, а не «файла просто нет»."""
    stage = _audio_stage_source()

    assert "proc = await run_cancellable_process(" in stage
    assert "if proc.returncode != 0:" in stage
    assert "raise AudioError(" in stage
