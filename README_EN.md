# MP3Bot — Telegram media conversion and AI-assisted analysis

**[Русский README](README.md)** · **[Architecture](ARCHITECTURE.md)** · **[Contributing](CONTRIBUTING.md)** · **[Security](SECURITY.md)**

MP3Bot is a locally operated Telegram bot for extracting audio from video and playlists, preparing Russian-language editorial materials, and handling English-to-Russian video dubbing through Yandex LiveDub. It can produce MP3 files, time-coded summaries, transcripts, Telegraph pages, and optional short-form media. The project is developed and maintained by Fedor Milovanov; its editorial and publication decisions remain human-reviewed.

**Operational boundary:** This README describes the existing code, not permission to run a bot against a production Telegram account or publish content. Follow the repository's [agent rules](AGENTS.md), [security policy](SECURITY.md), and service-specific runbooks before any live operation.

## User-facing modes

| Mode | Outputs |
|---|---|
| **RUS** | MP3 with artwork and metadata, topic, description, timestamps and quotations; synopsis and optional Telegraph pages, Shorts and clips |
| **ENG Full** | Full AI analysis, Russian LiveDub video, *clean Russian* MP3, final mixed MP3 extracted from the delivered video, plus translation-quality checks |
| **ENG Quick** | Dubbed video and both MP3 variants, without a full synopsis or semantic QA |
| **ENG Quick QA** | Quick-mode deliverables with lightweight QA for short videos |

For each successful LiveDub conversion the two MP3 variants are separate deliverables: the clean translated track and the mixed audio taken from the **final validated video**. The project independently probes both files with FFprobe, checks their durations against the video, tracks separate Telegram file IDs, and does not treat a missing/corrupt variant as a complete result.

Production ENG dubbing is **Yandex Live Voices only**. It must report an error rather than silently substitute generic TTS voices; `LIVEDUB_TTS_FALLBACK=0` is an enforced startup policy. Translation QA groups overlapping major-issue intervals and rejects silently truncated partial fixes.

## Runtime requirements

- **Python 3.12 or 3.13** for the supported bot runtime (the CI test matrix also covers Python 3.11);
- FFmpeg and FFprobe available in `PATH`;
- a running **local** `telegram-bot-api` server and a working network path to it;
- Node.js 22+ or Deno 2.3+ for modern YouTube challenge handling;
- credentials for the configured services, including Yandex OAuth for creating new LiveDub translations;
- optional dependencies for Whisper subtitles, PDF generation, and GPU rendering, as described in [requirements](requirements.txt) and [environment reference](docs/env-reference.md).

**There is no silent cloud Bot API fallback in the production entry point.** The bot checks its local Bot API connection before it begins large downloads. A Python/yt-dlp proxy is not a substitute for connectivity from the local `telegram-bot-api` process.

## Local setup

Install the project dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a local `.env` following [`.env.example`](.env.example). Required values depend on the enabled modes; the usual set includes `BOT_TOKEN`, `GEMINI_API_KEY`, `LOCAL_BOT_API_URL`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and, for new LiveDub jobs, `VOT_API_TOKEN` (or `YANDEX_OAUTH_TOKEN`). **Never commit or paste real tokens, cookies, hashes, or downloaded user media.**

Use the supported entry point; do not start an old launcher in parallel:

```bash
python bot_new.py          # Linux / macOS
py -3.13 bot_new.py        # Windows
```

On Windows, `Start Bot.bat` is also available. `bot_new.py` performs environment checks, enforces a single running instance, starts or verifies the local Bot API, and applies runtime safeguards before polling. The command `mp3bot --version` (or `python bot_new.py --version`) reports the package version without requiring network or database startup.

## AI model routing and synopsis quality

Model names and role assignments have a **single source of truth:** [`core/model_catalog.py`](core/model_catalog.py). Production code uses `primary` and `utility` roles instead of scattering provider model identifiers through the code. The primary semantic route uses high-quality reasoning for synopsis, publication text, translation QA and Shorts; mechanical tasks may use the utility route.

Cross-model quality downgrade is **disabled by default** (`MP3BOT_FALLBACK_MODEL=`). A provider-capacity error (503) is handled separately from project quota/rate limiting (429); multiple API keys for the same Google Cloud project do **not** multiply its quota. The active supported models, pricing-tier constraints and migration notes are maintained in the [Russian README](README.md#gemini-policy), the generated [model catalog](docs/model-catalog.md), and [Gemini operations](docs/gemini-operations.md). Consult these rather than copying a model ID from a dated example.

For a near-verbatim synopsis, available timed YouTube captions may supply a chronological scaffold. Captions with insufficient coverage are rejected; this is not a claim that the AI produces a certified verbatim transcript.

## Architecture and source of truth

```text
bot_new.py   -> environment bootstrap and single-instance guard
main.py      -> Telegram application lifecycle
handlers/    -> commands, callbacks and menus
pipelines/   -> end-to-end user scenarios
services/    -> Gemini, dubbing, media, Telegraph and PDF services
converters/  -> text / Telegram / Telegraph formatting
core/        -> configuration, storage, prompts, shared models
tools/       -> diagnostics, verification and maintenance
tests/       -> regression and integration tests
```

The dependency direction is `handlers → pipelines → services → converters → core`. [`ARCHITECTURE.md`](ARCHITECTURE.md) documents the current exceptions and the import-linter contract; [`CONTRIBUTING.md`](CONTRIBUTING.md) explains the development process. Before editing configuration or model policy, inspect the owning code and the generated documents—do not edit generated model catalogs by hand.

Useful Telegram commands include `/start`, `/help`, `/mode`, `/settings`, `/archive`, `/search`, `/segments`, `/cut`, `/status`, `/metrics`, and `/codehealth`. Availability depends on user permissions and the configured runtime.

## Verification and security

Install the CI-locked development environment as described in [AGENTS.md](AGENTS.md), then run the repository verifier before submitting a change:

```bash
python -m pip install -r requirements-lock.txt
python tools/verify_repo.py
```

The verifier exercises compilation, tests, Ruff and Git diff checks. Additional focused checks described in the Russian README include `python tools/check_code_health.py`, `python tools/check_requirements_lock.py`, and `python tools/audit_requirements_lock.py` (the latter requires `pip-audit`). CI includes a dependency audit, full-history Gitleaks scanning, Windows checks, and FFmpeg/FFprobe integration tests.

Do not run multiple bot instances; do not commit `.env`, OAuth credentials, cookies, or user data. After audio-mixing or QA-policy changes, review cache fingerprints, and inspect startup diagnostics after dependency/model upgrades. Report vulnerabilities privately according to [SECURITY.md](SECURITY.md); a public issue is not an appropriate place for secrets.

## License and attribution

The repository's code is distributed under the [MIT License](LICENSE), © 2026 Fedor Milovanov. Third-party dependencies, external APIs, AI model providers, and the Telegram/Yandex services retain their own terms; MIT licensing of this repository does not grant rights to third-party content or media.
