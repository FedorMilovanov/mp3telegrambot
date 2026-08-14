# 2026-08-15 — Source-owned Gemini 3.6 production re-audit

## Scope

Re-audited the current production `main` after the previous Gemini 3.6 resilience merge instead of treating green CI as proof that every source owner encoded the same quality policy.

## Verified external contract

- Current public Gemini API production Flash route remains `gemini-3.6-flash`; the unverified `gemini-3.7-flash` route remains excluded.
- Gemini 3.x semantic requests use thinking levels and do not intentionally add deprecated sampling controls such as `temperature`.
- Gemini 3.5 Flash-Lite / 3.5 Flash remain utility-only for mechanical extraction, routing, classification and machine-structure normalization.
- Priority inference remains explicit opt-in; Standard is the default.

## Findings

1. User-visible LiveDub info still encoded a 3.5-Lite/minimal default and depended on a later runtime patch to become 3.6/HIGH.
2. Publication core encoded the same weak default and depended on Factory resilience import order.
3. Inline publication generation and the title second-chance path still contained `minimal` semantic calls.
4. `configure_gemini_policy()` had weaker standalone fallbacks that were normally masked by the earlier max-quality bootstrap.
5. `.env.example` still documented 3.5 for title/description/publication work.
6. Factory retry-cache status text still described the prepared Gemini input as lossless after the compact AAC migration.
7. CI still used action majors that triggered the GitHub Actions Node 20 deprecation warning and did not explicitly run the repository's canonical `tools/verify_repo.py` contract.

## Fixes

- `livedub_info.py`, `livedub_publication.py`, `livedub_publication_core.py` and `livedub_info_presentation.py` now directly own exact `gemini-3.6-flash` + HIGH for user-visible semantic generation, with sampling-free structured configs and no semantic 3.5 fallback.
- Stale model/fallback environment overrides are refused or neutralized for semantic routes; 3.5/Lite remains a separate utility lane only.
- Factory resilience now verifies the publication owner invariant instead of monkey-patching publication selectors at runtime.
- LiveDub quality runtime verifies the native owner contract rather than rewriting the model selectors after import.
- Deterministic publication fallbacks are metadata-only and do not invent sermon theses, Scripture references or content claims.
- `.env.example` now documents the same semantic/utility split, Factory analysis AAC settings, Priority opt-in and `large-v3` ASR policy as production.
- Factory retry diagnostics now say `analysis-аудио`; the historical cache policy identifier is intentionally retained so already-verified cache entries are not invalidated only by terminology cleanup.
- CI uses current Node 24 action majors (`checkout@v6`, `setup-python@v6`, `upload-artifact@v7`) and runs `python tools/verify_repo.py` on Python 3.11 in addition to the existing Python 3.11, Python 3.13 and Windows gates.

## Regression contract

Tests now fail if a user-visible publication/info owner reintroduces `thinking_level="minimal"`, sampling controls, a 3.5 semantic route, runtime selector mutation, stale ENV guidance, obsolete CI action majors, or lossless wording for the compact analysis-audio path.

No Whisper model, Factory three-pass review, score threshold, boundary gate, render source, LiveDub media quality or user-visible semantic quality gate was weakened by this audit.
