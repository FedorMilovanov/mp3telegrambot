from pathlib import Path

path = Path(__file__).with_name("apply_audit_hardening_once.py")
text = path.read_text(encoding="utf-8")
start_marker = "# Reuse one uploaded Gemini file for the proven 15/30s 503 retries on a client."
end_marker = "# 8) Fix the brittle canonical CI assertion"
start = text.index(start_marker)
end = text.index(end_marker)

replacement = r"""# Reuse one uploaded Gemini file for the proven 15/30s 503 retries on a client.
sub_once(
    "services/gemini_analyze.py",
    r'''(?m)^(?P<indent>\s*)for client in GEMINI_CLIENTS:\n(?P=indent)    if success:\n(?P=indent)        break\n(?P=indent)    for attempt in range\(3\):$''',
    r'''\g<indent>for client in GEMINI_CLIENTS:\n\g<indent>    if success:\n\g<indent>        break\n\g<indent>    audio_part = None\n\g<indent>    for attempt in range(3):''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''(?m)^(?P<indent>\s*)audio_part = None  # инициализируем до upload, чтобы except не поймал NameError\n(?P=indent)audio_part, used_client = await upload_to_client\(client\)\n(?P=indent)used_audio_part = audio_part$''',
    r'''\g<indent>if audio_part is None:\n\g<indent>    audio_part, used_client = await upload_to_client(client)\n\g<indent>    used_audio_part = audio_part''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''(?m)^(?P<indent>\s*)# FIX: удаляем загруженный временный файл при ротации\n(?P=indent)# ключа независимо от размера\. Раньше порог >20MB оставлял\n(?P=indent)# файлы 0–20MB висеть в Gemini Files API при каждой смене ключа\.\n(?P=indent)if audio_part is not None and hasattr\(audio_part, 'name'\):\n(?P=indent)    _spawn_safe_delete\(client, audio_part\.name\)\n(?P=indent)last_err = e$''',
    r'''\g<indent>last_err = e''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''(?m)^(?P<indent>\s*)if _is_quota:\n(?P=indent)    # Не баним модель глобально \(ключи в разных проектах имеют свои квоты\)\n(?P=indent)    break\n(?P=indent)if _is_timeout:\n(?P=indent)    break  # 3 ретрая на этом ключе уже были — следующий клиент$''',
    r'''\g<indent>if _is_quota:\n\g<indent>    # Не баним модель глобально (ключи в разных проектах имеют свои квоты)\n\g<indent>    if audio_part is not None and hasattr(audio_part, "name"):\n\g<indent>        _spawn_safe_delete(client, audio_part.name)\n\g<indent>    break\n\g<indent>if _is_timeout:\n\g<indent>    if audio_part is not None and hasattr(audio_part, "name"):\n\g<indent>        _spawn_safe_delete(client, audio_part.name)\n\g<indent>    break  # ReadTimeout helper already exhausted this client''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''(?m)^(?P<indent>\s*)# AUDIT FIX 503-RETRY: на первых попытках ждём и повторяем тем же ключом\n(?P=indent)if _is_overload and attempt < 2:\n(?P=indent)    _wait_503 = 15 \* \(attempt \+ 1\)  # 15s, 30s\n(?P=indent)    logger\.info\(f"Gemini 503: жду \{_wait_503\}s и повторяю тем же ключом \(попытка \{attempt\+2\}/3\)\.\.\."\)\n(?P=indent)    await asyncio\.sleep\(_wait_503\)\n(?P=indent)    continue  # следующая попытка на ЭТОМ же ключе$''',
    r'''\g<indent># Bounded recovery keeps the same uploaded audio on this client.\n\g<indent>if _is_overload and attempt < 2:\n\g<indent>    _wait_503 = 15 * (attempt + 1)  # 15s, 30s\n\g<indent>    logger.info(\n\g<indent>        f"Gemini 503: жду {_wait_503}s и повторяю тем же ключом "\n\g<indent>        f"на уже загруженном аудио (попытка {attempt+2}/3)..."\n\g<indent>    )\n\g<indent>    await asyncio.sleep(_wait_503)\n\g<indent>    continue''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''(?m)^(?P<indent>\s*)break  # все попытки на ключе исчерпаны — следующий клиент$''',
    r'''\g<indent>if audio_part is not None and hasattr(audio_part, "name"):\n\g<indent>    _spawn_safe_delete(client, audio_part.name)\n\g<indent>break  # bounded 503 recovery exhausted — next client''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''(?ms)^            # AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг\n            if response is None and last_err is not None and \(not is_quota_error\(last_err\)\) and is_overload_error\(last_err\):\n.*?^                        continue\n''',
    '''            if (\n                response is None\n                and last_err is not None\n                and not is_quota_error(last_err)\n                and is_overload_error(last_err)\n            ):\n                logger.warning(\n                    "Gemini 503 recovery exhausted across configured clients; "\n                    "second full re-upload circle is disabled"\n                )\n''',
)

"""

path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
print("AUDIT_PATCHER_GEMINI_SECTION_FIXED")
