#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def replace_once(path: str, old: str, new: str, label: str) -> None:
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, got {count}")
    write(path, text.replace(old, new, 1))


# ── Polling reliability: move behavior to the Application owner. ──────────
write(
    "services/polling_reliability_runtime.py",
    '''#!/usr/bin/env python3\n"""Pending-update reliability policy used directly by the bot Application owner.\n\nRecent Telegram commands survive process restarts, stale backlog is rejected before\nnormal handlers run, and polling transport failures are logged explicitly.  This\nmodule does not replace PTB class methods.\n"""\nfrom __future__ import annotations\n\nimport logging\nimport os\nimport time\nfrom datetime import datetime, timezone\nfrom typing import Any, MutableMapping\n\nlogger = logging.getLogger(__name__)\n\n\ndef _bounded_env_seconds(name: str, *, default: int, minimum: int, maximum: int) -> int:\n    raw = os.getenv(name)\n    try:\n        value = default if raw is None or not raw.strip() else int(raw.strip())\n    except (TypeError, ValueError, OverflowError):\n        value = default\n    return max(minimum, min(value, maximum))\n\n\ndef _max_stale_noncommand_age() -> int:\n    return _bounded_env_seconds(\n        "BOT_PENDING_NONCOMMAND_MAX_AGE_SEC", default=900, minimum=60, maximum=24 * 3600\n    )\n\n\ndef _max_stale_command_age() -> int:\n    return _bounded_env_seconds(\n        "BOT_PENDING_COMMAND_MAX_AGE_SEC", default=6 * 3600, minimum=5 * 60, maximum=24 * 3600\n    )\n\n\ndef _message_age_seconds(update: Any) -> float | None:\n    message = getattr(update, "effective_message", None)\n    stamp = getattr(message, "date", None)\n    if stamp is None:\n        return None\n    try:\n        if stamp.tzinfo is None:\n            stamp = stamp.replace(tzinfo=timezone.utc)\n        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())\n    except Exception:\n        return None\n\n\ndef _is_command(update: Any) -> bool:\n    message = getattr(update, "effective_message", None)\n    text = str(getattr(message, "text", "") or "").lstrip()\n    return text.startswith("/")\n\n\ndef _stale_pending_reason(update: Any) -> tuple[str | None, float | None]:\n    age = _message_age_seconds(update)\n    if age is None:\n        return None, None\n    if _is_command(update):\n        if age > _max_stale_command_age():\n            return "stale-command", age\n        return None, age\n    if age > _max_stale_noncommand_age():\n        return "stale-noncommand", age\n    return None, age\n\n\ndef accept_pending_update(update: Any, bot_data: MutableMapping[str, Any]) -> bool:\n    """Record a live update or reject stale backlog before normal handlers run."""\n    stale_reason, age = _stale_pending_reason(update)\n    command = _is_command(update)\n    if stale_reason is not None:\n        logger.warning(\n            "🧹 Pending Telegram update dropped: type=%s update_id=%s age=%.0fs",\n            stale_reason,\n            getattr(update, "update_id", "?"),\n            age or 0.0,\n        )\n        return False\n\n    bot_data["telegram_last_update_monotonic"] = time.monotonic()\n    bot_data["telegram_last_update_id"] = getattr(update, "update_id", None)\n    if command:\n        message = getattr(update, "effective_message", None)\n        text = str(getattr(message, "text", "") or "").split(maxsplit=1)[0]\n        user = getattr(update, "effective_user", None)\n        logger.info(\n            "📥 Telegram command received: %s user=%s update_id=%s age=%.1fs",\n            text[:80],\n            getattr(user, "id", "?"),\n            getattr(update, "update_id", "?"),\n            age or 0.0,\n        )\n    return True\n\n\ndef polling_error_callback(error: BaseException) -> None:\n    """PTB ``start_polling`` error callback with full traceback evidence."""\n    logger.error(\n        "Telegram getUpdates error: %s: %s",\n        type(error).__name__,\n        error,\n        exc_info=(type(error), error, error.__traceback__),\n    )\n\n\ndef install_polling_reliability_runtime() -> str:\n    """Compatibility validator; Application composition is source-owned in main.py."""\n    if not callable(accept_pending_update) or not callable(polling_error_callback):\n        raise RuntimeError("polling reliability helpers are unavailable")\n    return "source-owned Application pending-update guard; no PTB method replacement"\n\n\n__all__ = [\n    "accept_pending_update",\n    "install_polling_reliability_runtime",\n    "polling_error_callback",\n]\n''',
)

replace_once(
    "main.py",
    '''from telegram.ext import (\n    Application, CommandHandler, MessageHandler,\n    CallbackQueryHandler, PollAnswerHandler, filters,\n)''',
    '''from telegram.ext import (\n    Application, ApplicationHandlerStop, CommandHandler, MessageHandler, TypeHandler,\n    CallbackQueryHandler, PollAnswerHandler, filters,\n)''',
    "main PTB imports",
)
replace_once(
    "main.py",
    '''    app = builder.build()\n    # FIX 2026-06-10: edited-команды''',
    '''    app = builder.build()\n\n    # Source-owned polling reliability: inspect every update before normal\n    # handlers without replacing PTB class methods globally.\n    from services.polling_reliability_runtime import (\n        accept_pending_update,\n        polling_error_callback,\n    )\n\n    async def _pending_update_guard(update, context):\n        del context\n        if not accept_pending_update(update, app.bot_data):\n            raise ApplicationHandlerStop\n\n    app.add_handler(TypeHandler(Update, _pending_update_guard), group=-100)\n\n    # FIX 2026-06-10: edited-команды''',
    "main pending guard",
)
replace_once(
    "main.py",
    '''            await app.updater.start_polling(\n                allowed_updates=Update.ALL_TYPES,\n                drop_pending_updates=True,\n            )''',
    '''            await app.updater.start_polling(\n                allowed_updates=Update.ALL_TYPES,\n                drop_pending_updates=False,\n                error_callback=polling_error_callback,\n            )''',
    "main polling start",
)

# Runtime manifest no longer installs a PTB method replacement.
path = "services/runtime_manifest.py"
text = read(path)
block = '''    RuntimeFeature(\n        "polling-reliability",\n        "services.polling_reliability_runtime",\n        "install_polling_reliability_runtime",\n        RuntimePhase.PRE_MAIN,\n    ),\n'''
if text.count(block) != 1:
    raise SystemExit("polling manifest block mismatch")
write(path, text.replace(block, "", 1))

# Expand the permanent anti-surgery gate to the newly source-owned surfaces.
replace_once(
    "tests/test_no_runtime_surgery_contract.py",
    '''    "services/pre_main_policy.py",\n    "services/gemini_max_quality.py",''',
    '''    "services/pre_main_policy.py",\n    "services/gemini_max_quality.py",\n    "services/polling_reliability_runtime.py",\n    "services/restart_state_runtime.py",\n    "services/bot_lifecycle.py",\n    "main.py",''',
    "anti-surgery surface expansion",
)
replace_once(
    "tests/test_no_runtime_surgery_contract.py",
    '''    "Message.reply_audio =",\n    "yandex.get_live_dub_audio =",''',
    '''    "Message.reply_audio =",\n    "Updater.start_polling =",\n    "Application.process_update =",\n    "yandex.get_live_dub_audio =",''',
    "anti-surgery polling tokens",
)

# Conspect contract is a pure schema/normalization contract now; update stale docs/tests.
path = "services/conspect_quality_contract.py"
text = read(path)
text = text.replace(
    "The production prompt is intentionally large and historically fragile.  This module\\ninstalls a small, late, idempotent contract before ``services.telegraph_pages``\\nimports its prompt/schema helpers.  It has three goals:",
    "The production prompt is intentionally large and historically fragile. This module\\nprovides pure Study schema/normalization helpers; prompt ownership lives in\\n``services.study_synthesis_policy`` and ``services.telegraph_pages``. It has three goals:",
)
text = text.replace(
    "The patch deliberately does not rewrite ``SYNOPSIS_PROMPT_V2``.",
    "No runtime patching is performed and ``SYNOPSIS_PROMPT_V2`` is never rewritten.",
)
write(path, text)

path = "tests/test_conspect_quality_contract.py"
text = read(path)
pattern = r'def test_install_preserves_synopsis_and_hardens_only_study\(\) -> None:.*?(?=\ndef test_hardened_study_prompt)'
replacement = '''def test_contract_validator_preserves_prompts_and_source_owns_study() -> None:\n    from core import prompts\n    from services.conspect_quality_contract import (\n        build_hardened_study_prompt,\n        install_conspect_quality_contract,\n    )\n\n    synopsis_before = prompts.SYNOPSIS_PROMPT_V2\n    qa_before = prompts.SYNOPSIS_PROMPT_QA\n    study_before = prompts.STUDY_ANALYSIS_PROMPT\n\n    status = install_conspect_quality_contract()\n    hardened = build_hardened_study_prompt(study_before)\n\n    assert prompts.SYNOPSIS_PROMPT_V2 == synopsis_before\n    assert prompts.SYNOPSIS_PROMPT_QA == qa_before\n    assert prompts.STUDY_ANALYSIS_PROMPT == study_before\n    assert "OPERATOR CONSPECT CONTRACT 2026-07-23" in hardened\n    assert "Ключевые слова в контексте Писания" in hardened\n    assert "2–5 содержательных карточек" in hardened\n    assert "0–3 блока; отсутствие блока является нормальным результатом" in hardened\n    assert "no runtime patching" in status\n\n    telegraph = Path("services/telegraph_pages.py").read_text(encoding="utf-8")\n    assert (\n        "from services.study_synthesis_policy import "\n        "TEACHERLY_STUDY_PROMPT as STUDY_ANALYSIS_PROMPT"\n    ) in telegraph\n\n\n'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"conspect test replacement count={count}")
write(path, text)

# Gemini quality tests target pre-main env + core config owner, not deleted wrappers.
write(
    "tests/test_gemini_max_quality.py",
    '''"""Regression contracts for the project-wide quality/cost policy."""\nfrom pathlib import Path\n\nfrom services import gemini_max_quality as quality\n\n\ndef test_heavy_quality_policy_forces_36_high_and_large_v3(monkeypatch):\n    for name in (\n        "GEMINI_MODEL", "GEMINI_MAX_MODEL", "LIVEDUB_INFO_MODEL",\n        "LIVEDUB_QUICK_QA_MODEL", "LIVEDUB_LONG_QA_MODEL", "LIVEDUB_QA_VERIFY_MODEL",\n        "SHORTS_FACTORY_MODEL", "GEMINI_FORCE_THINKING_LEVEL",\n        "LIVEDUB_QUICK_QA_THINKING", "LIVEDUB_LONG_QA_THINKING",\n        "LIVEDUB_QA_VERIFY_THINKING", "LIVEDUB_INFO_THINKING",\n        "WHISPER_MODEL", "WHISPER_ENG_SUBTITLES_MODEL", "SHORTS_FACTORY_WHISPER_MODEL",\n    ):\n        monkeypatch.delenv(name, raising=False)\n    diagnostic = quality.configure_max_quality_env()\n    import os\n    for name in (\n        "GEMINI_MODEL", "GEMINI_MAX_MODEL", "LIVEDUB_INFO_MODEL",\n        "LIVEDUB_QUICK_QA_MODEL", "LIVEDUB_LONG_QA_MODEL", "LIVEDUB_QA_VERIFY_MODEL",\n        "SHORTS_FACTORY_MODEL",\n    ):\n        assert os.environ[name] == "gemini-3.6-flash"\n    for name in (\n        "GEMINI_FORCE_THINKING_LEVEL", "LIVEDUB_QUICK_QA_THINKING",\n        "LIVEDUB_LONG_QA_THINKING", "LIVEDUB_QA_VERIFY_THINKING", "LIVEDUB_INFO_THINKING",\n    ):\n        assert os.environ[name] == "high"\n    for name in ("WHISPER_MODEL", "WHISPER_ENG_SUBTITLES_MODEL", "SHORTS_FACTORY_WHISPER_MODEL"):\n        assert os.environ[name] == "large-v3"\n    assert "semantic=gemini-3.6-flash/high" in diagnostic\n\n\ndef test_model_aware_thinking_is_owned_by_core_config_helper():\n    from core.globals import _effective_thinking_level\n    assert _effective_thinking_level("gemini-3.6-flash", "minimal") == "high"\n    assert _effective_thinking_level("gemini-3.5-flash-lite", "high") == "minimal"\n    assert _effective_thinking_level("gemini-3.5-flash", "high") == "minimal"\n    assert _effective_thinking_level("gemini-custom-audio-model", "medium") == "medium"\n\n\ndef test_utility_work_uses_35_quota_without_semantic_fallback():\n    src = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")\n    assert '_LIGHT_MODEL = "gemini-3.5-flash-lite"' in src\n    assert '_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"' in src\n    assert 'os.environ["GEMINI_LIGHT_ALLOW_MAIN_FALLBACK"] = "0"' in src\n    assert 'os.environ["LIVEDUB_PUBLICATION_FALLBACK_MODELS"] = ""' in src\n    assert 'os.environ["LIVEDUB_PUBLICATION_ALLOW_STRONG_FALLBACK"] = "0"' in src\n    assert "gemini-3.1" not in src and "gemini-2.5" not in src\n\n\ndef test_publication_metadata_directly_owns_36_high_quality_route():\n    publication = Path("services/livedub_publication_core.py").read_text(encoding="utf-8")\n    resilience = Path("services/gemini36_factory_resilience.py").read_text(encoding="utf-8")\n    assert '_PUBLICATION_MODEL = "gemini-3.6-flash"' in publication\n    assert 'thinking_level="high"' in publication\n    assert "GEMINI_LIGHT_MODEL" not in publication\n    assert "temperature=" not in publication\n    assert "_verify_publication_quality_route" in resilience\n    assert "publication.publication_models =" not in resilience\n\n\ndef test_pre_main_manifest_owns_quality_before_core_clients():\n    package = Path("services/__init__.py").read_text(encoding="utf-8")\n    manifest = Path("services/runtime_manifest.py").read_text(encoding="utf-8")\n    owner = Path("services/pre_main_policy.py").read_text(encoding="utf-8")\n    assert "configure_max_quality_env()" not in package\n    assert '"pre-main-quality-policy"' in manifest\n    assert '"services.pre_main_policy"' in manifest\n    assert owner.index("configure_gemini_qa_policy()") < owner.index("configure_max_quality_env()")\n    assert owner.index("configure_max_quality_env()") < owner.index("configure_gemini_policy()")\n    assert "core.globals" not in owner\n\n\ndef test_env_migration_preserves_semantic_36_utility_35_split():\n    src = Path("scripts/migrate-gemini-36.ps1").read_text(encoding="utf-8")\n    assert 'GEMINI_MODEL" -Value "gemini-3.6-flash"' in src\n    assert 'SHORTS_FACTORY_MODEL" -Value "gemini-3.6-flash"' in src\n    assert 'GEMINI_FORCE_THINKING_LEVEL" -Value "high"' in src\n    assert 'LIVEDUB_QUICK_QA_THINKING" -Value "high"' in src\n    assert 'LIVEDUB_LONG_QA_THINKING" -Value "high"' in src\n    assert 'LIVEDUB_INFO_THINKING" -Value "high"' in src\n    assert 'GEMINI_LIGHT_MODEL" -Value "gemini-3.5-flash-lite"' in src\n    assert 'GEMINI_LIGHT_FALLBACK_MODELS" -Value "gemini-3.5-flash"' in src\n    assert 'GEMINI_LIGHT_ALLOW_MAIN_FALLBACK" -Value "0"' in src\n    assert 'SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS" -Value "128"' in src\n    assert 'SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE" -Value "48000"' in src\n    assert 'GEMINI_SERVICE_TIER" -Value "priority"' in src\n    assert 'WHISPER_MODEL" -Value "large-v3"' in src\n    assert 'WHISPER_ENG_SUBTITLES_MODEL" -Value "large-v3"' in src\n    assert "gemini-3.1" not in src and "gemini-2.5" not in src\n''',
)

# Polling regression tests use public helper + explicit main composition.
path = "tests/test_polling_reliability_runtime.py"
text = read(path)
text = text.replace("from telegram.ext import Application, Updater\n\n", "")
pattern = r'def test_runtime_is_installed_on_services_import\(\) -> None:.*\Z'
replacement = '''def test_application_owner_wires_polling_reliability_without_ptb_monkey_patch() -> None:\n    main_source = Path("main.py").read_text(encoding="utf-8")\n    runtime_source = Path("services/polling_reliability_runtime.py").read_text(encoding="utf-8")\n    manifest_source = Path("services/runtime_manifest.py").read_text(encoding="utf-8")\n    assert "TypeHandler(Update, _pending_update_guard)" in main_source\n    assert "group=-100" in main_source\n    assert "accept_pending_update(update, app.bot_data)" in main_source\n    assert "drop_pending_updates=False" in main_source\n    assert "error_callback=polling_error_callback" in main_source\n    assert "Updater.start_polling =" not in runtime_source\n    assert "Application.process_update =" not in runtime_source\n    assert '"polling-reliability"' not in manifest_source\n\n\ndef test_accept_pending_update_records_live_command() -> None:\n    data = {}\n    update = _update("/mode", age_seconds=1)\n    assert runtime.accept_pending_update(update, data) is True\n    assert data["telegram_last_update_id"] == 123\n    assert data["telegram_last_update_monotonic"] > 0\n\n\ndef test_accept_pending_update_rejects_stale_backlog() -> None:\n    data = {}\n    assert runtime.accept_pending_update(_update("https://example.test", age_seconds=901), data) is False\n    assert data == {}\n'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"polling test replacement count={count}")
text = text.replace("from types import SimpleNamespace\n", "from types import SimpleNamespace\nfrom pathlib import Path\n")
write(path, text)

# Teacherly Study policy is imported by the page owner directly.
path = "tests/test_teacherly_study_synthesis.py"
text = read(path)
pattern = r'def test_teacherly_runtime_is_final_prompt_layer\(\) -> None:.*?(?=\ndef test_agent_contract)'
replacement = '''def test_teacherly_policy_is_source_owned_by_telegraph_pages() -> None:\n    package = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")\n    telegraph = (ROOT / "services" / "telegraph_pages.py").read_text(encoding="utf-8")\n    runtime = (ROOT / "services" / "study_synthesis_runtime.py").read_text(encoding="utf-8")\n    assert "install_teacherly_study_runtime()" not in package\n    assert (\n        "from services.study_synthesis_policy import "\n        "TEACHERLY_STUDY_PROMPT as STUDY_ANALYSIS_PROMPT"\n    ) in telegraph\n    assert "performs no mutation" in runtime\n    assert "STUDY_ANALYSIS_PROMPT =" not in runtime\n\n\n'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"teacherly test replacement count={count}")
write(path, text)

# R10 audit must inspect the base QA implementation, not the orchestration wrapper.
path = "tests/test_v3_audit_r10_eng.py"
text = read(path)
old = '''def _qa_fn_src() -> str:\n    src = _read("services/livedub_qa.py")\n    return src.split("async def run_translation_qa", 1)[1].split("\\ndef ", 1)[0]\n'''
new = '''def _qa_fn_src() -> str:\n    src = _read("services/livedub_qa.py")\n    return src.split("async def _run_translation_qa_base", 1)[1].split(\n        "\\n\\nasync def run_translation_qa", 1\n    )[0]\n'''
if text.count(old) != 1:
    raise SystemExit("R10 QA source helper anchor mismatch")
write(path, text.replace(old, new, 1))

# LiveDub cache/publication tests reflect the unified publication card + companion transaction.
path = "tests/livedub_qa_cases.py"
text = read(path)
text = text.replace(
    '''    # протухший file_id: чистим кэш и сообщаем, НЕ оставляем юзера молча\n    assert "Кэшированный перевод устарел" in src\n''',
    '''    # протухший video/file-id или companion pair: rollback + понятное сообщение\n    assert "deliver_cached_companions" in src\n    assert "delete_message_best_effort" in src\n    assert "Кэшированный перевод или его MP3-комплект устарел" in src\n''',
    1,
)
pattern = r'def test_livedub_info_card_sent_for_cached_file_id_too\(\):.*?(?=\ndef test_quick_qa_report_sent_after_video_not_before)'
replacement = '''def test_cached_livedub_uses_unified_publication_card_and_companion_transaction():\n    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")\n    helper = src[src.index("async def _send_livedub_result"):src.index("performer, title = parse_title")]\n    cached_block = helper[helper.index("if _livedub_cached_file_id and context:"):helper.index("if not live_dub_task:")]\n    assert "format_video_caption(_publication_card" in cached_block\n    assert "deliver_cached_companions" in cached_block\n    assert "_send_livedub_info_card_once" not in helper\n\n\n'''
text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count != 1:
    raise SystemExit(f"cached publication test replacement count={count}")
pattern = r'def test_livedub_info_card_wired_for_eng_quick_and_quick_qa\(\):.*?(?=\ndef test_livedub_light_model_env_documented)'
replacement = '''def test_quick_modes_share_unified_publication_metadata_without_second_ai_card():\n    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")\n    assert 'user_mode in ("eng_fast", "eng_fast_qa")' in src\n    assert "build_publication_card" in src\n    assert "format_video_caption(_publication_card" in src\n    assert "_send_livedub_info_card_once" not in src\n    assert "format_livedub_info_message" not in src\n    assert "get_translation_subtitles(video_url, workdir)" in src\n\n\n'''
text, count2 = re.subn(pattern, replacement, text, count=1, flags=re.S)
if count2 != 1:
    raise SystemExit(f"quick publication test replacement count={count2}")
write(path, text)

# Staging files must not enter the tested commit.
Path(__file__).unlink(missing_ok=True)
Path(".github/workflows/remaining-runtime-contracts-once.yml").unlink(missing_ok=True)
