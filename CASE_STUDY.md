# Case study: evidence-driven Telegram media and dubbing workflow

## Problem

Converting a video to an MP3 is straightforward; producing a **trustworthy, reviewable Telegram publication** from an external video is not. A long recording can involve unreliable source acquisition, language dubbing, transcription errors, unsupported theological claims, multipart Telegraph output, inconsistent audio deliverables, provider limits and restarts during media processing.

MP3Bot addresses those boundaries as an integration and quality-control system. It is **not** a new speech model, a new video platform or a claim that AI-written summaries are automatically faithful to source material.

## Implementation and decisions

| Boundary | Repository implementation | Why it matters |
|---|---|---|
| Safe single-process bootstrap | [bot_new.py](bot_new.py), [services/bot_lifecycle.py](services/bot_lifecycle.py) | Verifies runtime preconditions and prevents accidental duplicate bot instances before polling |
| Clear dependency direction | [ARCHITECTURE.md](ARCHITECTURE.md), [.import-linter](.import-linter), [tools/check_import_layers.py](tools/check_import_layers.py) | Enforces `handlers → pipelines → services → converters → core` instead of unchecked cross-layer imports |
| Local durable data and migration | [core/database.py](core/database.py), [services/database_migrations.py](services/database_migrations.py) | SQLite and migration contracts keep state separate from downloaded media, provider sessions and UI progress |
| Source-grounded analysis and publication | [core/model_catalog.py](core/model_catalog.py), [converters/md_telegraph.py](converters/md_telegraph.py), [AGENTS.md](AGENTS.md) | Distinguishes AI model roles, transcript-like synopsis, evidence-backed study material and corrected Telegraph markup |
| Dubbing output integrity | [services/yandex_live_dub.py](services/yandex_live_dub.py), [services/livedub_audio_companion.py](services/livedub_audio_companion.py) | Clean dubbed audio and final mixed-media audio are separate deliverables; a broken or missing one must not be counted as complete |
| Recovery and provider limits | [docs/runtime-reliability-live-canary.md](docs/runtime-reliability-live-canary.md), [docs/dependency-policy.md](docs/dependency-policy.md) | Offline code checks and real operator-controlled provider canaries have different evidence requirements |
| Long-term traceability | [docs/generated_pages_archive.md](docs/generated_pages_archive.md), [docs/quality_audit_history.md](docs/quality_audit_history.md) | Public links and editorial quality decisions remain inspectable; historical logs do not prove current provider state |

## Reproduce code-level verification

The canonical verifier is [tools/verify_repo.py](tools/verify_repo.py). In an authorized checkout with the Python version and dependency floor documented by [README_EN.md](README_EN.md):

```sh
python -m pip install -r requirements-lock.txt
python tools/verify_repo.py
```

The verifier checks compilation, generated indexes/catalogs, pytest, Ruff, mypy, import direction and diff hygiene. CI adds dependency/security, Windows and media-tool checks. Because the bot operates with local media, an actual Local Bot API process and external providers, **a green source build does not prove that a live Telegram message was delivered, a Yandex translation was successful or a Telegraph publication is currently accessible**. Those claims require dated provider-side evidence.

## Measured outcome versus unsupported claims

**Verifiable from the source:** a defined single-entry workflow, code-enforced layer boundaries, durable local state, explicit separation of dubbed audio outputs, publication/repair policy and repeatable test gates.

**Not established by this case:** bot uptime, number of successful publications, output error-rate improvements, theological accuracy on every recording, paid revenue, user growth, a safety certification, or copyright clearance for external media. Those require their own audit or rights review.

[Ownership and rights](OWNER.md) · [Architecture](ARCHITECTURE.md) · [Security policy](SECURITY.md) · [Russian README](README.md).
