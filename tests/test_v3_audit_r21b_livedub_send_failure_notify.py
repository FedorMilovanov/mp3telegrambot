#!/usr/bin/env python3
"""AUDIT R21b: два побочных бага, найденных при расследовании живого лога
"[LiveDub] cover не принят (Request Entity Too Large)" / "[LiveDub] fail:
Request Entity Too Large".

1. Ретрай без cover в `_send_livedub_result()` слепо срабатывал на ЛЮБОЕ
   исключение из первой попытки send_video(cover=...), включая ошибки
   размера payload — которые от удаления cover никак не лечатся. Это
   тратило время на гарантированно провальный повторный аплоад тяжёлого
   видео и логировало вводящее в заблуждение "cover не принят" для
   ошибки, которая с cover вообще не связана.

2. Финальный `except Exception as e:` в `_send_livedub_result()` (после
   исчерпания ретрая) молча возвращал False — юзер не получал ВООБЩЕ
   никакого объяснения, в отличие от соседних веток (превышение размера
   ДО отправки, таймаут генерации), которые уже уведомляют.
"""
from pathlib import Path

SRC = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")


def _send_livedub_result_body() -> str:
    start = SRC.find("async def _send_livedub_result()")
    assert start != -1, "_send_livedub_result не найден"
    # Следующая функция того же уровня вложенности начинается с "        performer, title = parse_title"
    end = SRC.find("performer, title = parse_title(full_title, channel_name)", start)
    assert end != -1
    return SRC[start:end]


def test_cover_retry_does_not_blindly_swallow_size_errors():
    body = _send_livedub_result_body()
    idx = body.find("except Exception as _cov_err:")
    assert idx != -1
    window = body[idx:idx + 700]
    assert "entity too large" in window.lower(), (
        "ретрай без cover должен пропускать (raise) ошибки размера payload, "
        "а не слепо повторять отправку — cover тут ни при чём"
    )


def test_send_failure_notifies_user_instead_of_silent_false():
    body = _send_livedub_result_body()
    idx = body.rfind("except Exception as e:")
    assert idx != -1, "финальный catch-all не найден"
    window = body[idx:idx + 2000]
    assert "context.bot.send_message" in window, (
        "финальный catch-all должен уведомлять юзера о неудачной отправке, "
        "а не просто return False молча"
    )
    assert "return False" in window


def test_send_failure_notice_hints_at_local_bot_api_when_size_related():
    body = _send_livedub_result_body()
    idx = body.rfind("except Exception as e:")
    window = body[idx:idx + 2000]
    assert "LOCAL_BOT_API_URL" in window
    assert "get_max_file_size_mb" in window
