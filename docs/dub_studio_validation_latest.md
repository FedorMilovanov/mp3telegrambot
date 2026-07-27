# Dub Studio Windows validation

- Result: **FAIL**
- Source commit: ba4b929f98711523dd38e78987d094b5a095ed78
- Dependencies: success
- Compile + recipe JSON: success
- Mode/worker contract: success
- Focused tests: success
- Runtime imports: success
- Ruff: failure
- Physical YouTube/Gemini/VoxCPM2 render: local-only; verify with /dubcheck and one real project per mode.

## dub-validation-contract.log
2026-07-27 18:25:34,226 - core.database - INFO - 🎨 Миграция: красивые субтитры Shorts включены по умолчанию
2026-07-27 18:25:34,261 - services.ffmpeg - WARNING - yt-dlp.conf просит --cookies-from-browser, но профиль cookies не найден — пропускаю конфиг; положите cookies.txt или настройте YTDLP_COOKIES_FROM_BROWSER
2026-07-27 18:25:34,263 - services.ffmpeg - WARNING - ⚠️ Нет cookies — YouTube может блокировать запросы
🧠 Gemini policy: main=gemini-3.6-flash, quality=gemini-3.6-flash, light=gemini-3.5-flash-lite, fallback=gemini-3.5-flash; thinking=high for quality tasks; publication metadata=minimal; translation_qa[quick_qa=gemini-3.6-flash,long_qa=gemini-3.6-flash,qa_verify=gemini-3.6-flash; thinking=high; audio_trust=1; confirm=1; migrated=LIVEDUB_QUICK_QA_MODEL,LIVEDUB_LONG_QA_MODEL,LIVEDUB_QA_VERIFY_MODEL]
🌐 Gemini route: system route/TUN
🎞 Shorts visual policy: moving=crop_zoom; 2-probe static slide=full_frame_blur; errors keep crop
📚 Conspect quality: verbatim Synopsis preserved; deep Study + contextual word studies enforced; new thin word studies dropped; legacy lexicon preserved; typos/timestamps repaired; material-led Study prose; one-page depth budget; no checklist/cards; organic lexical analysis; no-op audit retries rejected
Gemini/direct/worker contracts: OK

## dub-validation-tests.log
.................................                                        [100%]

## dub-validation-imports.log
2026-07-27 18:25:43,814 - services.ffmpeg - WARNING - yt-dlp.conf просит --cookies-from-browser, но профиль cookies не найден — пропускаю конфиг; положите cookies.txt или настройте YTDLP_COOKIES_FROM_BROWSER
2026-07-27 18:25:43,814 - services.ffmpeg - WARNING - ⚠️ Нет cookies — YouTube может блокировать запросы
🧠 Gemini policy: main=gemini-3.6-flash, quality=gemini-3.6-flash, light=gemini-3.5-flash-lite, fallback=gemini-3.5-flash; thinking=high for quality tasks; publication metadata=minimal; translation_qa[quick_qa=gemini-3.6-flash,long_qa=gemini-3.6-flash,qa_verify=gemini-3.6-flash; thinking=high; audio_trust=1; confirm=1; migrated=LIVEDUB_QUICK_QA_MODEL,LIVEDUB_LONG_QA_MODEL,LIVEDUB_QA_VERIFY_MODEL]
🌐 Gemini route: system route/TUN
🎞 Shorts visual policy: moving=crop_zoom; 2-probe static slide=full_frame_blur; errors keep crop
📚 Conspect quality: verbatim Synopsis preserved; deep Study + contextual word studies enforced; new thin word studies dropped; legacy lexicon preserved; typos/timestamps repaired; material-led Study prose; one-page depth budget; no checklist/cards; organic lexical analysis; no-op audit retries rejected
All focused runtime imports: OK

## dub-validation-ruff.log
B023 Function definition does not bind loop variable `calls`
  --> tests\test_semantic_tts_guard.py:92:21
   |
90 |                     retry_badcase_ratio_threshold=0,
91 |                 ):
92 |                     calls.append({"reference": reference_wav_path, "prompt": None, "retry": retry_badcase})
   |                     ^^^^^
93 |                     return [0]
94 |             else:
   |

B023 Function definition does not bind loop variable `calls`
   --> tests\test_semantic_tts_guard.py:108:21
    |
106 |                     retry_badcase_ratio_threshold=0,
107 |                 ):
108 |                     calls.append({"reference": prompt_wav_path, "prompt": prompt_text, "retry": retry_badcase})
    |                     ^^^^^
109 |                     return [0]
    |

Found 2 errors.
