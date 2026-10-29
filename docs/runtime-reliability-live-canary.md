# Live canary: sanitized evidence для точного SHA

Документ описывает операторский прогон Telegram → Local Bot API → обработка → provider/publication → доставка → restart recovery и подготовку доказательств без секретов и пользовательских медиа.

## Граница ответственности

`tools/verify_live_canary_evidence.py` — offline-проверка **полноты и формы** JSON. Он не вызывает Telegram, Gemini, Telegraph или LiveDub и не может подтвердить подлинность внешних событий. Успешный вывод означает только то, что комплект заполнен полностью и привязан к нужному SHA. Перед закрытием `MP3-REFACTOR-CANARY-001` reviewer должен также проверить sanitized proof-артефакты.

Допустимы два источника:

- `operator_canary` — новый контролируемый прогон с легитимными credentials;
- `production_telemetry` — уже существующая независимая телеметрия, которая доказывает **все** границы для того же SHA.

Unit/integration tests, mocks, HTTP 200 без валидного Telegram JSON и результаты другого коммита не заменяют живое доказательство.

## 1. Подготовить контролируемый контур

1. Зафиксировать у reviewer полный `expected_sha` и развернуть именно его, без незакоммиченных изменений. Не переносить `v4.0.0` и не подменять SHA строкой версии.
2. Передать `MP3BOT_BUILD_SHA=<полный SHA>` через защищённое runtime-окружение. Сверить startup `Runtime build`: `dirty=no`, а укороченный SHA совпадает с началом полного `git rev-parse HEAD`.
3. Использовать отдельного тестового бота и тестовый чат. Источник должен принадлежать оператору и не содержать приватных данных. Платные provider-вызовы допустимы только с разрешения владельца.
4. Секреты остаются в штатном `.env`/secret manager. Их нельзя копировать в evidence, чат, Git, AuditRepo или этот документ.
5. Поднять Local Bot API по [`runtime-reliability.md`](runtime-reliability.md). Успешный `/getMe` обязателен; одного открытого TCP-порта недостаточно.

## 2. Выполнить живой прогон

В одном связанном наборе прогонов на одном SHA нужно наблюдать и сохранить технические следы всех boundary ID:

| ID | Что должно быть доказано |
|---|---|
| `telegram` | Update принят ботом; polling отвечает валидным JSON с `ok=true` и массивом объектов `result`, а не только HTTP 200. |
| `local_bot_api` | `/getMe` успешен и фактический bot/file endpoint использует Local Bot API, без незамеченного cloud fallback. |
| `download` | Источник скачан и прошёл входную проверку; в evidence остаются только технические результаты, не имя/содержимое файла. |
| `conversion` | FFmpeg/FFprobe завершились успешно, выход непустой и проверен по потокам/длительности. |
| `gemini` | Реальный запрошенный provider route вернул принятый результат; deterministic fallback не выдаётся за provider success. |
| `telegraph` | Публикация/проверка Telegraph завершилась успешно; ссылки и тексты в proof не включаются. |
| `livedub` | LiveDub generation/mix/QA завершены, итоговый MP4 содержит проверяемую русскую аудиодорожку. |
| `delivery` | Видео и положенные companion-результаты доставлены; commit-after-complete сработал без rollback. |
| `restart_recovery` | После завершения доставки процесс перезапущен: clean startup, polling heartbeat, чтение сохранённого состояния и отсутствие повторной доставки подтверждены. |

Отдельно обязателен **Large Media** witness:

- размер фактически доставленного файла `> 50 МиБ` и `<= 2000 МиБ`;
- маршрут именно `local_bot_api`, не cloud compression/fallback;
- размер получен из готового файла, а не оценки или подписи;
- sanitized proof подтверждает успешную доставку этого же результата.

Если какая-либо граница не выполнялась, завершилась ошибкой или доказывается только mock/test, evidence пока неполноценно. Нельзя ставить `passed` заранее.

## 3. Санитизировать proof до вычисления digest

Для каждой границы сохраняется минимальный технический excerpt: timestamp, название события, результат, SHA build, размер/длительность/кодеки там, где это нужно. До передачи reviewer удаляются:

- токены, API keys/hashes, cookies, proxy credentials и Authorization headers;
- тексты updates и generated content, имена пользователей, chat/user/message IDs;
- source/video/audio files, thumbnails, PDF и другие пользовательские артефакты;
- локальные пути, URL source/Telegraph и query strings;
- `.env`, SQLite dumps и полные production logs.

`proof_sha256` вычисляется от **уже санитизированного** файла. Например, в PowerShell:

```powershell
(Get-FileHash -Algorithm SHA256 $SanitizedProof).Hash.ToLowerInvariant()
```

Один sanitized файл может подтверждать несколько границ, поэтому digest может повторяться. Сам JSON содержит только allowlisted поля; свободный `notes`, raw logs и вложенные URLs намеренно отклоняются.

## 4. Сформировать и проверить evidence

Команда печатает заведомо неполный шаблон. Его `pending`, `unknown`, пустые timestamps/digests и `false` sanitization flags не проходят проверку.

```powershell
py -3.12 tools\verify_live_canary_evidence.py --expected-sha $ExpectedSha --template
```

Сохраните вывод как UTF-8 JSON **вне Git-рабочего дерева** и заполните только по фактическим результатам:

- `started_at`, `completed_at` и каждый `observed_at` — ISO-8601 с timezone;
- `completed_at >= started_at`, все события находятся внутри этого окна;
- `commit_sha` и `runtime.build_sha` равны `expected_sha`, `runtime.dirty=no`;
- каждый boundary встречается ровно один раз и имеет `result=passed` и SHA-256 sanitized proof;
- `large_media.size_bytes` — целое число байт, не строка и не Boolean;
- все три sanitization flags становятся `true` только после фактической очистки proof.

Проверка результата:

```powershell
py -3.12 tools\verify_live_canary_evidence.py --expected-sha $ExpectedSha $EvidenceJson
```

Ожидаемая диагностика заканчивается словами `sanitized proof artifacts still require independent review`. Это намеренное ограничение, а не формальное закрытие аудита. Reviewer получает JSON и sanitized proof-артефакты; исходные секреты, медиа и неочищенные логи не передаются.
