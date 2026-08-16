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


def test_main_pipeline_external_commands_use_async_process_owner() -> None:
    source, function = _process_function_source()

    assert "from services.async_process import run_cancellable_process" in source
    assert "subprocess.run(" not in function
    assert function.count("await run_cancellable_process(") == 5


def test_main_pipeline_thread_normalizer_remains_owned() -> None:
    source, function = _process_function_source()

    assert "await_owned_coroutine" in source
    assert function.count(
        "await await_owned_coroutine(\n"
        "                        asyncio.to_thread(normalize_mp3_lossless"
    ) >= 1
    assert function.count("asyncio.to_thread(normalize_mp3_lossless") == 2


def test_recompression_uses_atomic_mp3_conversion_owner() -> None:
    source, function = _process_function_source()

    assert "from services.mp3_conversion import reencode_mp3_64k_atomic" in source
    assert function.count("await reencode_mp3_64k_atomic(mp3_path, mp3_64_path)") == 2
    assert function.count("mp3_path.unlink(missing_ok=True)") == 2
    assert "_recompress_proc" not in function


def test_cached_audio_download_checks_exit_status() -> None:
    _source, function = _process_function_source()

    assert "_cached_audio_proc = await run_cancellable_process(" in function
    assert "_cached_audio_proc.returncode != 0" in function
    assert "Кэш аудио yt-dlp rc=" in function
