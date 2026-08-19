#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _path(name: str) -> Path:
    return ROOT / name


def replace_once(name: str, old: str, new: str) -> None:
    path = _path(name)
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{name}: expected one exact match, found {count}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def sub_once(name: str, pattern: str, repl: str, *, flags: int = 0) -> None:
    path = _path(name)
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, repl, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{name}: expected one regex match, found {count}: {pattern[:120]!r}")
    path.write_text(updated, encoding="utf-8")


def write_file(name: str, content: str) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# 1) Factory full-video acquisition must be fail-closed just like analysis audio.
replace_once(
    "services/shorts_factory_source.py",
    '''    command = list(YTDLP_BASE_ARGS) + _factory_quality_sort_reset() + [\n        "--format",\n        "bestvideo+bestaudio/best",\n''',
    '''    command = list(YTDLP_BASE_ARGS) + _factory_quality_sort_reset() + [\n        "--abort-on-unavailable-fragments",\n        "--format",\n        "bestvideo+bestaudio/best",\n''',
)
replace_once(
    "services/shorts_factory_source.py",
    '''            if media_probe_is_deliverable(probe):\n                logger.info(\n                    "Factory maximum-quality video source: %s %sx%s %.3fs %.1fMB",\n''',
    '''            if media_probe_is_deliverable(probe):\n                actual_duration = float(getattr(probe, "duration", 0.0) or 0.0)\n                if expected_duration > 0 and not factory_duration_matches(\n                    actual_duration,\n                    float(expected_duration),\n                ):\n                    logger.warning(\n                        "Factory rejected incomplete maximum-quality video source: "\n                        "expected=%.3fs actual=%.3fs file=%s",\n                        float(expected_duration),\n                        actual_duration,\n                        path.name,\n                    )\n                    continue\n                logger.info(\n                    "Factory maximum-quality video source: %s %sx%s %.3fs %.1fMB",\n''',
)

# 2) Retry-cache is an optimization: storage failure may fail open only after
# re-verifying the already prepared media artifact.
replace_once(
    "services/shorts_factory_retry_cache.py",
    '''    prepared = Path(await original_downloader(url, media_id))\n    await _store_analysis_audio(\n        url,\n        media_id,\n        prepared,\n        expected_duration=expected_duration,\n    )\n    return prepared\n''',
    '''    prepared = Path(await original_downloader(url, media_id))\n    try:\n        await _store_analysis_audio(\n            url,\n            media_id,\n            prepared,\n            expected_duration=expected_duration,\n        )\n    except asyncio.CancelledError:\n        raise\n    except (OSError, RuntimeError, ValueError) as exc:\n        from services.media_delivery_probe import probe_media_async\n        from services.shorts_factory_source import (\n            factory_audio_probe_is_usable,\n            factory_duration_matches,\n            measure_factory_audio_duration,\n        )\n\n        probe = await probe_media_async(prepared)\n        verified_duration = await measure_factory_audio_duration(prepared)\n        expected = float(expected_duration or 0.0)\n        integrity_ok = factory_audio_probe_is_usable(probe) and (\n            expected <= 0\n            or factory_duration_matches(verified_duration, expected)\n        )\n        if not integrity_ok:\n            raise RuntimeError(\n                "Factory retry-cache write failed and prepared analysis audio "\n                "did not pass mandatory re-verification"\n            ) from exc\n        logger.warning(\n            "Factory retry-cache storage failed after media re-verification; "\n            "continuing with verified analysis audio: %s: %s",\n            type(exc).__name__,\n            str(exc)[:400],\n        )\n    return prepared\n''',
)

# 3) Factory final capacity diagnostics must reflect actual per-client outcomes.
replace_once(
    "services/shorts_factory_capacity_runtime.py",
    "    capacity_overload = False\n",
    "    client_outcomes: list[str] = []\n",
)
replace_once(
    "services/shorts_factory_capacity_runtime.py",
    '''            action = factory_client_retry_action(exc)\n            logger.warning(\n''',
    '''            action = factory_client_retry_action(exc)\n            client_outcomes.append(action)\n            logger.warning(\n''',
)
replace_once(
    "services/shorts_factory_capacity_runtime.py",
    "                capacity_overload = True\n",
    "",
)
sub_once(
    "services/shorts_factory_capacity_runtime.py",
    r'''\n    if capacity_overload:\n        raise RuntimeError\(.*?\n        \) from last_error\n\n    raise RuntimeError\(\n        f"All Gemini clients failed strict Shorts Factory review: \{last_error\}"\n    \)''',
    '''\n    capacity_failures = sum(\n        1 for outcome in client_outcomes if outcome == "capacity"\n    )\n    if (\n        client_outcomes\n        and len(client_outcomes) == len(clients)\n        and capacity_failures == len(clients)\n    ):\n        raise RuntimeError(\n            "Gemini 3.7 сейчас перегружена (503/high demand). "\n            f"Все {len(clients)} настроенных API-клиента получили 503 после bounded "\n            "экспоненциальных повторов. Это НЕ означает, что API-ключи или квота "\n            "исчерпаны: 503 — ошибка доступности backend, а quota/rate-limit обычно "\n            "возвращается как 429. Качество не понижено: 3.6/3.5/Lite не "\n            "использовались. Analysis-аудио сохранено в retry-кэше примерно на "\n            f"{capacity.retry_cache_ttl_seconds() / 3600:.0f} ч — повторите Factory позже."\n        ) from last_error\n\n    if capacity_failures:\n        other_failures = len(client_outcomes) - capacity_failures\n        raise RuntimeError(\n            "Gemini 3.7 strict Factory review failed across configured clients: "\n            f"{capacity_failures}/{len(clients)} client(s) returned 503/high demand; "\n            f"{other_failures} client(s) failed for other reasons. "\n            "503 is backend availability, not proof of exhausted keys/quota. "\n            "Качество не понижено: 3.6/3.5/Lite не использовались."\n        ) from last_error\n\n    raise RuntimeError(\n        f"All Gemini clients failed strict Shorts Factory review: {last_error}"\n    )''',
    flags=re.DOTALL,
)

# 4) Pin bgutil JS runtime to the reviewed commit, not only a movable tag.
replace_once(
    "tools/ensure_bgutil_provider.py",
    'BGUTIL_VERSION = "1.3.1"\n',
    'BGUTIL_VERSION = "1.3.1"\nBGUTIL_COMMIT = "7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"\n',
)
replace_once(
    "tools/ensure_bgutil_provider.py",
    '''    return marker == BGUTIL_VERSION and GENERATE_SCRIPT.is_file()\n''',
    '''    expected = f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}"\n    return marker == expected and GENERATE_SCRIPT.is_file()\n''',
)
replace_once(
    "tools/ensure_bgutil_provider.py",
    '''        server = staging / "server"\n        _run([npm, "ci"], cwd=server)\n''',
    '''        head_process = subprocess.run(\n            [git, "rev-parse", "HEAD"],\n            cwd=str(staging),\n            capture_output=True,\n            text=True,\n            encoding="utf-8",\n            errors="replace",\n            timeout=15,\n            check=False,\n        )\n        head = (head_process.stdout or "").strip().lower()\n        if head_process.returncode != 0 or head != BGUTIL_COMMIT:\n            raise ProvisionError(\n                "Pinned bgutil tag resolved to an unexpected commit: "\n                f"expected={BGUTIL_COMMIT} actual={head or 'unknown'}"\n            )\n\n        server = staging / "server"\n        _run([npm, "ci"], cwd=server)\n''',
)
replace_once(
    "tools/ensure_bgutil_provider.py",
    '''            BGUTIL_VERSION + "\\n", encoding="utf-8"\n''',
    '''            f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}\\n", encoding="utf-8"\n''',
)
replace_once(
    "tools/ensure_bgutil_provider.py",
    '''    print(f"[SETUP] bgutil {BGUTIL_VERSION} installed: {SERVER_ROOT}")\n''',
    '''    print(\n        f"[SETUP] bgutil {BGUTIL_VERSION}@{BGUTIL_COMMIT[:8]} installed: "\n        f"{SERVER_ROOT}"\n    )\n''',
)

replace_once(
    "services/youtube_po_token_runtime.py",
    'BGUTIL_EXPECTED_VERSION = "1.3.1"\n',
    'BGUTIL_EXPECTED_VERSION = "1.3.1"\nBGUTIL_EXPECTED_COMMIT = "7608dd51ee813b48cf9a6d68c6e42cb197ce10e0"\n',
)
replace_once(
    "services/youtube_po_token_runtime.py",
    '''class YouTubePoTokenRuntime:\n    provider_version: str\n    node_version: str\n    provider_home: Path\n\n    def status_text(self) -> str:\n        return (\n            f"bgutil {self.provider_version}; node={self.node_version}; "\n            "browserless=on"\n        )\n''',
    '''class YouTubePoTokenRuntime:\n    provider_version: str\n    provider_commit: str\n    node_version: str\n    provider_home: Path\n\n    def status_text(self) -> str:\n        return (\n            f"bgutil {self.provider_version}@{self.provider_commit[:8]}; "\n            f"node={self.node_version}; browserless=on"\n        )\n''',
)
replace_once(
    "services/youtube_po_token_runtime.py",
    '''    if not generated.is_file():\n        raise YouTubePoTokenRuntimeError(\n            "browserless bgutil runtime не собран. Запусти 'Start Bot.bat': "\n            "он установит pinned bgutil provider в .runtime без Chrome."\n        )\n    return home\n''',
    '''    if not generated.is_file():\n        raise YouTubePoTokenRuntimeError(\n            "browserless bgutil runtime не собран. Запусти 'Start Bot.bat': "\n            "он установит pinned bgutil provider в .runtime без Chrome."\n        )\n    marker = home.parent / ".mp3bot-bgutil-version"\n    try:\n        marker_value = marker.read_text(encoding="utf-8").strip()\n    except OSError as exc:\n        raise YouTubePoTokenRuntimeError(\n            "browserless bgutil runtime не имеет commit marker; "\n            "перезапусти Start Bot.bat для безопасной пересборки"\n        ) from exc\n    expected_marker = f"{BGUTIL_EXPECTED_VERSION}@{BGUTIL_EXPECTED_COMMIT}"\n    if marker_value != expected_marker:\n        raise YouTubePoTokenRuntimeError(\n            "browserless bgutil runtime не соответствует pinned commit: "\n            f"expected={expected_marker} actual={marker_value or 'empty'}"\n        )\n    return home\n''',
)
replace_once(
    "services/youtube_po_token_runtime.py",
    '''    return YouTubePoTokenRuntime(\n        provider_version=provider_version,\n        node_version=node_version,\n        provider_home=provider_home,\n    )\n''',
    '''    return YouTubePoTokenRuntime(\n        provider_version=provider_version,\n        provider_commit=BGUTIL_EXPECTED_COMMIT,\n        node_version=node_version,\n        provider_home=provider_home,\n    )\n''',
)
replace_once(
    "services/youtube_po_token_runtime.py",
    '''    "BGUTIL_EXPECTED_VERSION",\n''',
    '''    "BGUTIL_EXPECTED_VERSION",\n    "BGUTIL_EXPECTED_COMMIT",\n''',
)

# 5) One-time WPC migration, not a pip uninstall on every startup.
replace_once(
    "Start Bot.bat",
    'set "SETUP_MARKER=%VENV_DIR%\\.setup-complete"\n',
    'set "SETUP_MARKER=%VENV_DIR%\\.setup-complete"\nset "WPC_MIGRATION_MARKER=%VENV_DIR%\\.wpc-provider-removed"\n',
)
sub_once(
    "Start Bot.bat",
    r'''rem Migration from the old WPC/nodriver browser provider\. pip install -r does\nrem not remove packages that disappeared from the lock, so explicitly remove\nrem the obsolete direct dependencies to guarantee Chrome cannot remain a\nrem hidden yt-dlp PO-token fallback in an upgraded existing \.venv\.\n"%VENV_PYTHON%" -m pip uninstall -y yt-dlp-getpot-wpc nodriver >nul 2>&1\nif errorlevel 1 \(\n    echo ERROR: Failed to remove the obsolete browser-based YouTube PO Token runtime\.\n    pause\n    exit /b 1\n\)\n''',
    '''rem One-time migration from the old WPC/nodriver browser provider.\nrem Keep it idempotent without paying a pip subprocess cost on every bot start.\nif not exist "%WPC_MIGRATION_MARKER%" (\n    "%VENV_PYTHON%" -m pip uninstall -y yt-dlp-getpot-wpc nodriver >nul 2>&1\n    if errorlevel 1 (\n        echo ERROR: Failed to remove the obsolete browser-based YouTube PO Token runtime.\n        pause\n        exit /b 1\n    )\n    >"%WPC_MIGRATION_MARKER%" echo browser-provider-removed-v1\n)\n''',
)

# 6) Direct entrypoint launches must own the repository cwd.
replace_once(
    "bot_new.py",
    '''import os\nimport sqlite3\nimport sys\n''',
    '''import os\nimport sqlite3\nimport sys\nfrom pathlib import Path\n''',
)
replace_once(
    "bot_new.py",
    '''_configure_stdio()\nos.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"\n''',
    '''_configure_stdio()\n_PROJECT_ROOT = Path(__file__).resolve().parent\nos.chdir(_PROJECT_ROOT)\nos.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"\n''',
)

# 7) Supplemental RUS/Telegraph semantic work is strict-primary: omit a section
# rather than silently publish Lite output.
replace_once(
    "services/telegraph_pages.py",
    '''# PATCH-FIX: lightweight per-process tracking of whether the last\n# _gemini_text_request call fell back to a non-primary model.\n# Used by the pipeline to surface lite-model warnings in the caption.\n_gemini_last_was_fallback: bool = False\n''',
    '''# Backward-compatible marker consumed by main_pipeline. Semantic Telegraph\n# generation is now strict-primary, so this remains False: an unavailable\n# primary model omits the optional section instead of downgrading to Lite.\n_gemini_last_was_fallback: bool = False\n''',
)
replace_once(
    "services/telegraph_pages.py",
    "                                allow_model_fallback: bool = True) -> str | None:\n",
    "                                allow_model_fallback: bool = False) -> str | None:\n",
)
replace_once(
    "services/telegraph_pages.py",
    '''    # PATCH-FIX: reset fallback flag at the start of each call\n    global _gemini_last_was_fallback\n    _gemini_last_was_fallback = False\n\n''',
    '''    # Quality policy: this semantic route never downgrades models.\n\n''',
)
replace_once(
    "services/telegraph_pages.py",
    '''    # ВСЕ на максимальном качестве — 3.5-flash\n    _models = [GEMINI_MODEL]\n    if allow_model_fallback and GEMINI_MODEL != "gemini-3.1-flash-lite":\n        _models.append("gemini-3.1-flash-lite")\n''',
    '''    # Semantic pages use only the configured production model (3.7 Flash).\n    _models = [GEMINI_MODEL]\n    if allow_model_fallback:\n        logger.warning(\n            "_gemini_text_request[%s]: model fallback requested but ignored by "\n            "strict semantic quality policy",\n            task,\n        )\n''',
)
sub_once(
    "services/telegraph_pages.py",
    r'''        if model_idx > 0:\n            logger\.warning\(\n                "_gemini_text_request: переключаюсь на модель %s \(#%d/%d\)",\n                model_name, model_idx \+ 1, len\(_models\),\n            \)\n            # PATCH-FIX: track fallback for downstream visibility\n            _gemini_last_was_fallback = True\n''',
    '''        if model_idx > 0:\n            raise AssertionError("semantic model fallback is disabled")\n''',
)

# Primary audio analysis is also hard-strict: emergency env cannot re-enable Lite.
sub_once(
    "services/gemini_analyze.py",
    r'''def _audio_fallback_models\(primary_model: str\) -> list\[str\]:\n    """Quality-first model list for audio analysis\..*?\n    return out\n\n''',
    '''def _audio_fallback_models(primary_model: str) -> list[str]:\n    """Return the single production audio model; semantic downgrade is forbidden."""\n    primary = str(primary_model or "").strip()\n    return [primary] if primary else []\n\n''',
    flags=re.DOTALL,
)

# Reuse one uploaded Gemini file for the proven 15/30s 503 retries on a client.
replace_once(
    "services/gemini_analyze.py",
    '''        for client in GEMINI_CLIENTS:\n            if success:\n                break\n            for attempt in range(3):\n''',
    '''        for client in GEMINI_CLIENTS:\n            if success:\n                break\n            audio_part = None\n            for attempt in range(3):\n''',
)
replace_once(
    "services/gemini_analyze.py",
    '''                    audio_part = None  # инициализируем до upload, чтобы except не поймал NameError\n                    audio_part, used_client = await upload_to_client(client)\n                    used_audio_part = audio_part\n''',
    '''                    if audio_part is None:\n                        audio_part, used_client = await upload_to_client(client)\n                        used_audio_part = audio_part\n''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''                    # FIX: удаляем загруженный временный файл при ротации\n                    # ключа независимо от размера\. Раньше порог >20MB оставлял\n                    # файлы 0–20MB висеть в Gemini Files API при каждой смене ключа\.\n                    if audio_part is not None and hasattr\(audio_part, 'name'\):\n                        _spawn_safe_delete\(client, audio_part\.name\)\n                    last_err = e\n''',
    '''                    last_err = e\n''',
)
replace_once(
    "services/gemini_analyze.py",
    '''                    if _is_quota:\n                        # Не баним модель глобально (ключи в разных проектах имеют свои квоты)\n                        break\n                    if _is_timeout:\n                        break  # 3 ретрая на этом ключе уже были — следующий клиент\n''',
    '''                    if _is_quota:\n                        # Не баним модель глобально (ключи в разных проектах имеют свои квоты)\n                        if audio_part is not None and hasattr(audio_part, "name"):\n                            _spawn_safe_delete(client, audio_part.name)\n                        break\n                    if _is_timeout:\n                        if audio_part is not None and hasattr(audio_part, "name"):\n                            _spawn_safe_delete(client, audio_part.name)\n                        break  # ReadTimeout helper already exhausted this client\n''',
)
replace_once(
    "services/gemini_analyze.py",
    '''                    if _is_overload and attempt < 2:\n                        _wait_503 = 15 * (attempt + 1)  # 15s, 30s\n                        logger.info(f"Gemini 503: жду {_wait_503}s и повторяю тем же ключом (попытка {attempt+2}/3)...")\n                        await asyncio.sleep(_wait_503)\n                        continue  # следующая попытка на ЭТОМ же ключе\n                    break  # все попытки на ключе исчерпаны — следующий клиент\n                    raise  # неизвестная ошибка — пробрасываем\n''',
    '''                    if _is_overload and attempt < 2:\n                        _wait_503 = 15 * (attempt + 1)  # 15s, 30s\n                        logger.info(\n                            f"Gemini 503: жду {_wait_503}s и повторяю тем же ключом "\n                            f"на уже загруженном аудио (попытка {attempt+2}/3)..."\n                        )\n                        await asyncio.sleep(_wait_503)\n                        continue\n                    if audio_part is not None and hasattr(audio_part, "name"):\n                        _spawn_safe_delete(client, audio_part.name)\n                    if _is_overload:\n                        break  # bounded 503 recovery exhausted — next client\n                    raise  # неизвестная ошибка — пробрасываем\n''',
)
sub_once(
    "services/gemini_analyze.py",
    r'''        # AUDIT FIX 503-RETRY: если все ключи упали с 503, ждём 60s и второй круг\n        if response is None and last_err is not None and \(not is_quota_error\(last_err\)\) and is_overload_error\(last_err\):\n            logger\.warning\("Gemini 503 на всех ключах — жду 60s и пробую ещё раз весь круг\.\.\."\)\n            await asyncio\.sleep\(60\)\n            for client in GEMINI_CLIENTS:\n                audio_part = None\n                try:\n                    _obs_retry_num = 3\n                    audio_part, used_client = await upload_to_client\(client\)\n                    response = await asyncio\.wait_for\(\n                        client\.aio\.models\.generate_content\(\n                            model=_current_model,\n                            contents=\[audio_part, prompt\],\n                            config=make_audio_config\(max_output_tokens=65536, model_name=_current_model\),\n                        \),\n                        timeout=960\.0,\n                    \)\n                    used_audio_part = audio_part\n                    # FIX AUDIT R4: без success=True следующая итерация цикла\n                    # моделей обнуляла response \(строки сброса в начале итерации\)\n                    # и выбрасывала успешный ответ второго круга\.\n                    success = True\n                    logger\.info\("Gemini: второй круг успешен!"\)\n                    break\n                except Exception as e2:\n                    logger\.warning\(f"Gemini второй круг: \{type\(e2\).__name__\}: \{str\(e2\)\[:150\]\}"\)\n                    # FIX AUDIT R4: зеркалим очистку первого круга — иначе каждый\n                    # неудачный клиент второго круга оставляет ~50MB файл в Files API\.\n                    if audio_part is not None and hasattr\(audio_part, 'name'\):\n                        _spawn_safe_delete\(client, audio_part\.name\)\n                    last_err = e2\n                    continue\n''',
    '''        if (\n            response is None\n            and last_err is not None\n            and not is_quota_error(last_err)\n            and is_overload_error(last_err)\n        ):\n            logger.warning(\n                "Gemini 503 recovery exhausted across configured clients; "\n                "second full re-upload circle is disabled"\n            )\n''',
)

# 8) Fix the brittle canonical CI assertion: behavior is already covered in the
# dedicated Factory capacity tests; keep constants as direct runtime assertions.
replace_once(
    "tests/test_gemini_max_quality.py",
    "from services import gemini_max_quality as quality\n",
    "from services import gemini_max_quality as quality\nfrom services import shorts_factory_capacity_runtime as capacity_runtime\n",
)
replace_once(
    "tests/test_gemini_max_quality.py",
    '''    assert "_FACTORY_CAPACITY_PASS_ATTEMPTS = 2" in capacity\n    assert "_FACTORY_CAPACITY_RETRY_BASE_SECONDS = 15.0" in capacity\n    assert "_FACTORY_CAPACITY_RETRY_MAX_SECONDS = 60.0" in capacity\n    assert "_FACTORY_CAPACITY_RETRY_JITTER_SECONDS = 5.0" in capacity\n    assert "Переключаюсь на следующий клиент без понижения модели" in capacity\n    assert "НЕ означает, что API-ключи или квота исчерпаны" in capacity\n    assert "3.6/3.5/Lite не" in capacity\n    assert "Factory analysis audio duration verified before Gemini" in capacity\n''',
    '''    assert capacity_runtime._FACTORY_CAPACITY_PASS_ATTEMPTS == 2\n    assert capacity_runtime._FACTORY_CAPACITY_RETRY_BASE_SECONDS == 15.0\n    assert capacity_runtime._FACTORY_CAPACITY_RETRY_MAX_SECONDS == 60.0\n    assert capacity_runtime._FACTORY_CAPACITY_RETRY_JITTER_SECONDS == 5.0\n    # User-facing 503/quota semantics are asserted behaviorally in\n    # tests/test_factory_capacity_fast_fail.py rather than by grepping source text.\n''',
)

# Update PO runtime tests for exact commit evidence.
replace_once(
    "tests/test_youtube_po_token_runtime.py",
    '''    assert runtime.provider_version == "1.3.1"\n    assert runtime.node_version == "22.14.0"\n    assert runtime.provider_home == provider_home\n    assert runtime.status_text() == "bgutil 1.3.1; node=22.14.0; browserless=on"\n''',
    '''    assert runtime.provider_version == "1.3.1"\n    assert runtime.provider_commit == po.BGUTIL_EXPECTED_COMMIT\n    assert runtime.node_version == "22.14.0"\n    assert runtime.provider_home == provider_home\n    assert runtime.status_text() == (\n        f"bgutil 1.3.1@{po.BGUTIL_EXPECTED_COMMIT[:8]}; "\n        "node=22.14.0; browserless=on"\n    )\n''',
)

write_file(
    "tests/test_factory_video_source_integrity.py",
    '''from __future__ import annotations\n\nimport asyncio\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services import shorts_factory_source as source\n\n\ndef _deliverable_probe(duration: float):\n    return SimpleNamespace(\n        duration=duration,\n        width=1920,\n        height=1080,\n        has_video=True,\n        has_audio=True,\n        audio_sample_rate=48000,\n        audio_codec="aac",\n    )\n\n\ndef test_factory_video_rejects_incomplete_source_and_aborts_missing_fragments(\n    monkeypatch, tmp_path\n):\n    seen: list[str] = []\n\n    async def fake_run(command, **_kwargs):\n        seen.extend(map(str, command))\n        (tmp_path / "abc_factory_max_source.mkv").write_bytes(b"x" * 4096)\n        return SimpleNamespace(returncode=0, stderr="")\n\n    async def fake_probe(_path):\n        return _deliverable_probe(80.0)\n\n    monkeypatch.setattr(source, "run_cancellable_process", fake_run)\n    monkeypatch.setattr(source, "probe_media_async", fake_probe)\n    monkeypatch.setattr(source, "media_probe_is_deliverable", lambda _probe: True)\n    monkeypatch.setattr(source, "ensure_factory_video_space", lambda *a, **k: None)\n\n    with pytest.raises(RuntimeError, match="without a probed maximum-quality"):\n        asyncio.run(\n            source.download_factory_video_source(\n                "https://example.invalid/video",\n                "abc",\n                tmp_path,\n                expected_duration=120.0,\n            )\n        )\n\n    assert "--abort-on-unavailable-fragments" in seen\n    assert "bestvideo+bestaudio/best" in seen\n\n\ndef test_factory_video_accepts_duration_verified_source(monkeypatch, tmp_path):\n    async def fake_run(command, **_kwargs):\n        path = tmp_path / "abc_factory_max_source.mkv"\n        path.write_bytes(b"x" * 4096)\n        return SimpleNamespace(returncode=0, stderr="")\n\n    async def fake_probe(_path):\n        return _deliverable_probe(119.9)\n\n    monkeypatch.setattr(source, "run_cancellable_process", fake_run)\n    monkeypatch.setattr(source, "probe_media_async", fake_probe)\n    monkeypatch.setattr(source, "media_probe_is_deliverable", lambda _probe: True)\n    monkeypatch.setattr(source, "ensure_factory_video_space", lambda *a, **k: None)\n\n    path = asyncio.run(\n        source.download_factory_video_source(\n            "https://example.invalid/video",\n            "abc",\n            tmp_path,\n            expected_duration=120.0,\n        )\n    )\n    assert path.name == "abc_factory_max_source.mkv"\n''',
)

write_file(
    "tests/test_factory_retry_cache_storage_policy.py",
    '''from __future__ import annotations\n\nimport asyncio\nfrom types import SimpleNamespace\n\nimport pytest\n\nfrom services import shorts_factory_retry_cache as cache\n\n\ndef test_cache_storage_failure_fails_open_only_after_reverification(monkeypatch, tmp_path):\n    prepared = tmp_path / "analysis.aac"\n    prepared.write_bytes(b"x" * 4096)\n\n    async def no_cache(*args, **kwargs):\n        return None\n\n    async def downloader(*args, **kwargs):\n        return prepared\n\n    async def storage_failure(*args, **kwargs):\n        raise OSError("cache disk unavailable")\n\n    async def probe(_path):\n        return SimpleNamespace(duration=120.0, has_audio=True, audio_sample_rate=48000, audio_codec="aac")\n\n    async def duration(_path):\n        return 120.0\n\n    import services.media_delivery_probe as media_probe\n    import services.shorts_factory_source as source\n\n    monkeypatch.setattr(cache, "_cached_analysis_audio", no_cache)\n    monkeypatch.setattr(cache, "_store_analysis_audio", storage_failure)\n    monkeypatch.setattr(media_probe, "probe_media_async", probe)\n    monkeypatch.setattr(source, "measure_factory_audio_duration", duration)\n    monkeypatch.setattr(source, "factory_audio_probe_is_usable", lambda p: bool(p and p.has_audio))\n    monkeypatch.setattr(source, "factory_duration_matches", lambda a, b: abs(a - b) <= 2.0)\n\n    result = asyncio.run(\n        cache.download_factory_audio_with_retry_cache(\n            "https://example.invalid/video",\n            "abc",\n            original_downloader=downloader,\n            expected_duration=120.0,\n        )\n    )\n    assert result == prepared\n\n\ndef test_cache_storage_failure_still_fails_closed_if_media_no_longer_verifies(monkeypatch, tmp_path):\n    prepared = tmp_path / "analysis.aac"\n    prepared.write_bytes(b"x" * 4096)\n\n    async def no_cache(*args, **kwargs):\n        return None\n\n    async def downloader(*args, **kwargs):\n        return prepared\n\n    async def storage_failure(*args, **kwargs):\n        raise OSError("cache disk unavailable")\n\n    async def probe(_path):\n        return SimpleNamespace(duration=30.0, has_audio=True, audio_sample_rate=48000, audio_codec="aac")\n\n    async def duration(_path):\n        return 30.0\n\n    import services.media_delivery_probe as media_probe\n    import services.shorts_factory_source as source\n\n    monkeypatch.setattr(cache, "_cached_analysis_audio", no_cache)\n    monkeypatch.setattr(cache, "_store_analysis_audio", storage_failure)\n    monkeypatch.setattr(media_probe, "probe_media_async", probe)\n    monkeypatch.setattr(source, "measure_factory_audio_duration", duration)\n    monkeypatch.setattr(source, "factory_audio_probe_is_usable", lambda p: bool(p and p.has_audio))\n    monkeypatch.setattr(source, "factory_duration_matches", lambda a, b: abs(a - b) <= 2.0)\n\n    with pytest.raises(RuntimeError, match="mandatory re-verification"):\n        asyncio.run(\n            cache.download_factory_audio_with_retry_cache(\n                "https://example.invalid/video",\n                "abc",\n                original_downloader=downloader,\n                expected_duration=120.0,\n            )\n        )\n''',
)

# Add mixed-outcome diagnostic test to the existing behavioral suite.
replace_once(
    "tests/test_factory_capacity_fast_fail.py",
    '''def test_duration_mismatch_fails_before_any_gemini_client(monkeypatch, tmp_path):\n''',
    '''def test_mixed_client_failures_do_not_claim_every_key_got_503(monkeypatch, tmp_path):\n    audio = tmp_path / "factory.flac"\n    audio.write_bytes(b"x" * 2048)\n    first = SimpleNamespace(name="first")\n    second = SimpleNamespace(name="second")\n\n    async def run_pass(client, **kwargs):\n        if client is first:\n            raise _ServiceError(503, "UNAVAILABLE: high demand")\n        raise _ServiceError(429, "RESOURCE_EXHAUSTED")\n\n    _install_fake_factory_modules(monkeypatch, run_pass)\n    _disable_capacity_retry_delay(monkeypatch)\n    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [first, second])\n\n    with pytest.raises(RuntimeError) as raised:\n        asyncio.run(\n            capacity_runtime.create_factory_plan_resumable(\n                audio, title="Title", performer="Author", duration=120\n            )\n        )\n    message = str(raised.value)\n    assert "1/2 client(s) returned 503" in message\n    assert "Все 2 настроенных API-клиента получили 503" not in message\n\n\ndef test_duration_mismatch_fails_before_any_gemini_client(monkeypatch, tmp_path):\n''',
)

# Strengthen the existing PO tests with exact runtime marker validation.
replace_once(
    "tests/test_youtube_po_token_runtime.py",
    '''def test_old_wpc_browser_stack_is_not_a_dependency() -> None:\n''',
    '''def test_bgutil_runtime_marker_must_match_exact_commit(monkeypatch, tmp_path):\n    server = tmp_path / "provider" / "server"\n    (server / "build").mkdir(parents=True)\n    (server / "build" / "generate_once.js").write_text("// ok", encoding="utf-8")\n    (server.parent / ".mp3bot-bgutil-version").write_text(\n        "1.3.1@wrong-commit\\n", encoding="utf-8"\n    )\n    monkeypatch.setenv("BGUTIL_PROVIDER_HOME", str(server))\n\n    with pytest.raises(po.YouTubePoTokenRuntimeError, match="pinned commit"):\n        po._require_provider_build()\n\n\ndef test_old_wpc_browser_stack_is_not_a_dependency() -> None:\n''',
)

print("AUDIT_HARDENING_PATCH_APPLIED")
