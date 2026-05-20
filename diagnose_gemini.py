#!/usr/bin/env python3
"""
diagnose_gemini.py - диагностика Gemini API: текст, inline-audio, upload-audio.
Запускай через тот же Python, что и бот:
    py -3.13 diagnose_gemini.py

Автоматически читает .env (если установлен python-dotenv).
"""
import os
import sys
import asyncio
import time
from pathlib import Path

# --- 1. Попытка прочитать .env ---
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        print(f"[env] Loaded {env_path}")
    else:
        print(f"[env] .env not found at {env_path} - using system env only")
except ImportError:
    print("[env] python-dotenv не установлен - читаю только из системного окружения")
    print("      (установи: py -3.13 -m pip install python-dotenv)")

# --- 2. Собираем ВСЕ ключи (GEMINI_API_KEY, GEMINI_API_KEY_2, ...) ---
keys = []
primary = os.getenv("GEMINI_API_KEY", "").strip()
if primary:
    keys.append(("GEMINI_API_KEY", primary))
for i in range(2, 11):
    k = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
    if k:
        keys.append((f"GEMINI_API_KEY_{i}", k))

model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not keys:
    print("\n❌ Ни один GEMINI_API_KEY не задан (ни в .env, ни в окружении)")
    sys.exit(1)

print(f"\n[config] model = {model}")
print(f"[config] keys found: {len(keys)} -> {', '.join(name for name, _ in keys)}")

# --- 3. Импорт SDK ---
try:
    from google import genai
    from google.genai import types
    import google.genai as _g
    print(f"[sdk] google-genai version: {getattr(_g, '__version__', 'unknown')}")
except ImportError as e:
    print(f"\n❌ google-genai не установлен для этого Python: {e}")
    print("   Установи: py -3.13 -m pip install google-genai")
    sys.exit(1)

# Используем ПЕРВЫЙ ключ для тестов
key_name, key_value = keys[0]
print(f"[test] using {key_name} (last 4: ...{key_value[-4:]})")
client = genai.Client(api_key=key_value)


async def test_text():
    print(f"\n{'='*60}")
    print(f"TEST 1: Текстовый запрос (model={model})")
    print('='*60)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=["Скажи привет по-русски одним словом."],
            config=types.GenerateContentConfig(temperature=0.1),
        )
        fr = resp.candidates[0].finish_reason if resp.candidates else "NO_CANDIDATES"
        txt = (resp.text or "EMPTY")[:200]
        print(f"✅ OK | finish_reason={fr} | text={txt}")
        return True
    except Exception as e:
        print(f"❌ FAIL | {type(e).__name__}: {str(e)[:400]}")
        return False


def _find_mp3():
    """Ищем mp3 в downloads/ или в корне."""
    for d in ["downloads", "."]:
        p = Path(d)
        if not p.exists():
            continue
        mp3s = sorted(p.glob("*.mp3"), key=lambda x: x.stat().st_size)
        if mp3s:
            return mp3s[0]
    return None


async def test_inline_audio(mp3):
    size_mb = mp3.stat().st_size / (1024 * 1024)
    print(f"\n{'='*60}")
    print(f"TEST 2: Inline audio (file={mp3.name}, {size_mb:.1f} MB)")
    print(f"⚠️  Base64-запрос будет ~{size_mb * 1.33:.1f} MB (лимит inline ~20 MB)")
    print('='*60)
    try:
        audio_bytes = mp3.read_bytes()
        part = types.Part.from_bytes(data=audio_bytes, mime_type="audio/mpeg")
        t0 = time.time()
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=[part, "В одном предложении: о чём это аудио?"],
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=512),
            ),
            timeout=180.0,
        )
        dt = time.time() - t0
        fr = resp.candidates[0].finish_reason if resp.candidates else "NO_CANDIDATES"
        txt = (resp.text or "EMPTY")[:200]
        print(f"✅ OK ({dt:.1f}s) | finish_reason={fr} | text={txt}")
        return True
    except asyncio.TimeoutError:
        print(f"❌ FAIL | TimeoutError: запрос завис на 180+ секунд")
        return False
    except Exception as e:
        print(f"❌ FAIL | {type(e).__name__}: {str(e)[:400]}")
        return False


async def test_upload_audio(mp3):
    size_mb = mp3.stat().st_size / (1024 * 1024)
    print(f"\n{'='*60}")
    print(f"TEST 3: File API upload (file={mp3.name}, {size_mb:.1f} MB)")
    print('='*60)
    try:
        t0 = time.time()
        uf = await client.aio.files.upload(
            file=mp3,
            config=types.UploadFileConfig(mime_type="audio/mpeg", display_name="diagnose")
        )
        print(f"[upload] started: name={uf.name} state={uf.state}")
        # poll
        while str(uf.state).endswith("PROCESSING") and time.time() - t0 < 300:
            await asyncio.sleep(3)
            uf = await client.aio.files.get(name=uf.name)
            print(f"[upload] state={uf.state} elapsed={time.time()-t0:.1f}s")
        if str(uf.state).endswith("FAILED"):
            print("❌ FAIL | File processing failed on server")
            return False
        dt_up = time.time() - t0
        print(f"[upload] ready in {dt_up:.1f}s, calling generate_content...")

        t1 = time.time()
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=model,
                contents=[uf, "В одном предложении: о чём это аудио?"],
                config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=512),
            ),
            timeout=180.0,
        )
        dt = time.time() - t1
        fr = resp.candidates[0].finish_reason if resp.candidates else "NO_CANDIDATES"
        txt = (resp.text or "EMPTY")[:200]
        print(f"✅ OK (upload {dt_up:.1f}s + gen {dt:.1f}s) | finish_reason={fr} | text={txt}")

        # cleanup
        try:
            await client.aio.files.delete(name=uf.name)
            print(f"[upload] deleted {uf.name}")
        except Exception:
            pass
        return True
    except asyncio.TimeoutError:
        print(f"❌ FAIL | TimeoutError: запрос завис на 180+ секунд")
        return False
    except Exception as e:
        print(f"❌ FAIL | {type(e).__name__}: {str(e)[:400]}")
        return False


async def main():
    r1 = await test_text()

    mp3 = _find_mp3()
    if not mp3:
        print("\n⚠️  MP3 не найден в downloads/ или в корне — пропускаю audio-тесты")
        print("    Подложи любой .mp3 (или запусти бота на одно видео, чтобы он закэшировал)")
        r2 = r3 = None
    else:
        r2 = await test_inline_audio(mp3)
        r3 = await test_upload_audio(mp3)

    print(f"\n{'='*60}")
    print("ИТОГ:")
    print(f"  Текст:   {'✅ OK' if r1 else '❌ FAIL'}")
    print(f"  Inline:  {'✅ OK' if r2 else ('❌ FAIL' if r2 is False else '⚠️ SKIP')}")
    print(f"  Upload:  {'✅ OK' if r3 else ('❌ FAIL' if r3 is False else '⚠️ SKIP')}")
    print('='*60)

    if r1 and r2 is False and r3:
        print("\n🔴 Диагноз: inline-audio падает, upload-audio работает.")
        print("   Это та самая проблема, что в боте. Применяй:")
        print("   py -3.13 fix_audio_upload.py")
        print("   py -3.13 bot_new.py")
    elif r1 and r2 and r3:
        print("\n🟢 Всё работает! Значит проблема была транзиентной (Gemini был перегружен).")
        print("   Просто перезапусти бота — должно заработать.")
    elif not r1:
        print("\n🔴 Текст тоже падает — проблема в ключе/сети/модели, а не в аудио.")
        print("   Проверь:")
        print("   - правильность GEMINI_API_KEY")
        print("   - что GEMINI_MODEL=gemini-2.5-flash (или другая доступная)")
        print("   - что нет VPN/прокси, блокирующего generativelanguage.googleapis.com")
    elif r2 is None:
        print("\n🟡 Нет MP3 для audio-тестов — скачай любой файл и повтори.")
    else:
        print("\n🟡 Результаты неоднозначны — скинь полный вывод, разберёмся.")


if __name__ == "__main__":
    asyncio.run(main())
