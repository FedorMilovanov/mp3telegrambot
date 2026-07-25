#!/usr/bin/env python3
"""Validated entry point for MP3Bot. Run this file instead of main.py."""

import os
import sys

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

if sys.version_info < (3, 11):
    print("❌ Требуется Python 3.11+")
    print(f"   Текущая версия: {sys.version}")
    sys.exit(1)

from dotenv import load_dotenv

load_dotenv()

_bot_token = os.getenv("BOT_TOKEN", "").strip()
_gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

if not _bot_token:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: BOT_TOKEN не задан в .env!")
    print("   Создай файл .env и добавь: BOT_TOKEN=твой_токен")
    sys.exit(1)

if not _gemini_key:
    print("⚠️ GEMINI_API_KEY не задан — AI-функции будут недоступны")

try:
    from services.project_runtime_hardening import acquire_early_singleton

    if not acquire_early_singleton():
        print("❌ Бот уже запущен в другом процессе. Новый экземпляр завершён.")
        sys.exit(2)
except Exception as _singleton_error:
    print(f"⚠️ Ранний singleton guard недоступен: {_singleton_error}")

try:
    from services.local_botapi_required import require_local_bot_api

    require_local_bot_api()
except Exception as _local_error:
    print(f"❌ Local Bot API не поднялся: {_local_error}")
    print("   Включи системный TUN/VPN и запусти бот снова.")
    sys.exit(3)

try:
    from services.livedub_info_guard import install_livedub_info_guard

    install_livedub_info_guard()
except Exception as _info_guard_error:
    print(f"⚠️ Grounding описаний LiveDub не установлен: {_info_guard_error}")

try:
    from services.livedub_info_presentation import install_livedub_info_presentation

    install_livedub_info_presentation()
except Exception as _info_presentation_error:
    print(f"⚠️ Оформление русских заголовков LiveDub не установлено: {_info_presentation_error}")

try:
    from services.livedub_long_qa import install_livedub_long_qa

    install_livedub_long_qa()
except Exception as _long_qa_error:
    print(f"⚠️ Сегментная проверка длинных LiveDub не установлена: {_long_qa_error}")

try:
    from services.livedub_qa_trust import install_livedub_qa_trust

    install_livedub_qa_trust()
except Exception as _qa_trust_error:
    print(f"⚠️ Аудиопроверка точности LiveDub не установлена: {_qa_trust_error}")

try:
    from services.livedub_delivery_hardening import install_livedub_delivery_hardening

    install_livedub_delivery_hardening()
except Exception as _delivery_hardening_error:
    print(f"⚠️ Усиление доставки LiveDub не установлено: {_delivery_hardening_error}")

import main as _main_module
from main import main

# main.py still contains an old hand-maintained Gemini allow-list. Replace only
# those obsolete warnings before run_bot_async executes; the runtime policy that
# configured clients before this import remains the source of truth.
try:
    from services.gemini_startup_diagnostics import install_gemini_startup_diagnostics

    install_gemini_startup_diagnostics(_main_module)
except Exception as _gemini_diagnostics_error:
    print(f"⚠️ Актуальная Gemini startup-диагностика не установлена: {_gemini_diagnostics_error}")

# Replace the imported /help binding before Application handlers are built.
try:
    from services.livedub_help_runtime import install_livedub_help_runtime

    install_livedub_help_runtime(_main_module)
except Exception as _help_runtime_error:
    print(f"⚠️ Актуальная справка LiveDub не установлена: {_help_runtime_error}")

try:
    from services.livedub_output_policy import install_livedub_output_policy

    install_livedub_output_policy()
except Exception as _output_policy_error:
    print(f"⚠️ Русские заголовки LiveDub не установлены: {_output_policy_error}")

try:
    from services.livedub_publication import install_livedub_publication

    install_livedub_publication()
except Exception as _publication_error:
    print(f"⚠️ Публикационная карточка LiveDub не установлена: {_publication_error}")

try:
    from services.livedub_audio_companion import install_livedub_audio_companion

    install_livedub_audio_companion()
except Exception as _livedub_audio_error:
    print(f"⚠️ MP3-компаньон LiveDub не установлен: {_livedub_audio_error}")

try:
    from services.livedub_audio_quality_guard import install_livedub_audio_quality_guard

    install_livedub_audio_quality_guard()
except Exception as _audio_quality_guard_error:
    print(f"⚠️ Контроль качества двух MP3 LiveDub не установлен: {_audio_quality_guard_error}")

try:
    from services.livedub_audio_dedupe import install_livedub_audio_dedupe

    install_livedub_audio_dedupe()
except Exception as _livedub_dedupe_error:
    print(f"⚠️ Защита от исходного английского MP3 не установлена: {_livedub_dedupe_error}")

try:
    from services.livedub_output_policy import harden_livedub_audio_dedupe

    harden_livedub_audio_dedupe()
except Exception as _dedupe_hardening_error:
    print(f"⚠️ Усиленная защита от английского MP3 не установлена: {_dedupe_hardening_error}")

try:
    from services.livedub_deep_audit import install_livedub_deep_audit

    install_livedub_deep_audit()
except Exception as _deep_audit_error:
    print(f"⚠️ Глубокая защита публикации и QA LiveDub не установлена: {_deep_audit_error}")

try:
    from services.livedub_dual_audio_policy import install_livedub_dual_audio_policy

    install_livedub_dual_audio_policy()
except Exception as _dual_audio_policy_error:
    print(f"⚠️ Раздельное оформление двух MP3 LiveDub не установлено: {_dual_audio_policy_error}")

try:
    from services.project_runtime_hardening import install_project_runtime_hardening

    install_project_runtime_hardening(_main_module)
except Exception as _runtime_hardening_error:
    print(f"⚠️ Project runtime hardening не установлен: {_runtime_hardening_error}")

if __name__ == "__main__":
    main()
