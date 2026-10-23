# quality_audits — исторические отчёты

Каждый файл в этом каталоге — снимок состояния на дату в его имени (`20260820`,
`2026-08-15`). Модели, версии пакетов и маршруты, упомянутые внутри, читаются
как **история**, а не как текущая политика: часть утверждений (например
«текущая модель — 3.6 Flash») устарела.

Актуальные источники правды:

- `core/model_catalog.py` — модели, роли, деприкации, free tier;
- `docs/model-catalog.md` — сгенерированный обзор каталога;
- `README.md` — эксплуатационная политика;
- `docs/AUDIT_2026-10-22.md` — последний сквозной аудит и статусы находок.

| Отчёт | Дата | Тема |
|---|---|---|
| `2026-08-15-explicit-livedub-delivery.md` | 2026-08-15 | явная доставка LiveDub без monkey-patch |
| `2026-08-15-gemini36-resilience.md` | 2026-08-15 | устойчивость Gemini 3.6 |
| `2026-08-15-source-owned-gemini36-polish.md` | 2026-08-15 | source-owned полировка Gemini 3.6 |
| `bgutil-node-runtime-parity-20260820.md` | 2026-08-20 | паритет node-рантайма bgutil |
| `gemini-503-capacity-audit-20260820.md` | 2026-08-20 | 503/capacity |
| `latency-stage-measurement-20260820.md` | 2026-08-20 | измерение задержек по стадиям |
| `youtube-bgutil-offline-integrity-20260820.md` | 2026-08-20 | offline-целостность bgutil |
| `ytdlp-freshness-policy-20260820.md` | 2026-08-20 | политика свежести yt-dlp |
