# 2026-08-15 — Gemini 3.6 verified resilience audit

## Trigger

Production Shorts Factory log showed repeated `503 UNAVAILABLE / high demand` while a ~54 minute source had been converted to a ~262.6 MB FLAC solely for Gemini analysis.

A proposed migration to `gemini-3.7-flash` was re-verified before production merge and rejected: the official Gemini API model catalog currently identifies `gemini-3.6-flash` as the stable production Flash model. The unverified 3.7 model id is not part of this change.

## Verified policy

- semantic/user-visible work: `gemini-3.6-flash`, `thinking=high`;
- Factory remains three independent HIGH review passes with existing quality gates;
- LiveDub publication title/author/description is kept on 3.6/HIGH;
- `gemini-3.5-flash-lite` remains only for genuinely mechanical utility work;
- Whisper remains `large-v3`;
- no 3.5/2.x semantic fallback is introduced.

## Reliability changes

1. Gemini-only analysis audio is encoded as AAC mono, 128 kbps, 48 kHz by default. Original video, Yandex LiveDub media, render sources and Whisper source quality are not altered.
2. Factory 503 retries remain app-owned; the SDK Factory client already uses `HttpRetryOptions(attempts=1)`, so there is no nested retry cascade.
3. One HIGH pass now has four bounded same-client/same-upload attempts with exponential backoff (3 s base, 20 s cap, 2 s jitter). Persistent model-capacity overload still stops before a wasteful multi-key re-upload sweep and keeps the local retry cache.
4. `GEMINI_SERVICE_TIER=priority` is supported as an explicit operator opt-in. Priority is not forced because Gemini GenerateContent Priority requires an eligible Tier 2/3 project. Standard remains the safe default.
5. Existing Windows subprocess hardening was retained rather than duplicated: shared async subprocess output already decodes UTF-8 with replacement and the local Bot API child receives `PYTHONIOENCODING=utf-8`.

## Why AAC does not lower Gemini semantic input quality

Google's Gemini audio documentation states that supported audio is internally downsampled to 16 Kbps and multichannel input is combined to a single channel. The 128 kbps mono AAC analysis surrogate therefore remains substantially above the model's documented analysis resolution while avoiding the former hundreds-of-megabytes lossless upload.

## Regression coverage

- bounded 503 exhaustion still does not sweep the next API key;
- a transient 503 reuses the same already-uploaded audio;
- 429 still rotates clients without reducing model quality;
- compact analysis audio is AAC/mono/128 kbps/48 kHz and never FLAC;
- low-fidelity analysis env overrides are clamped upward;
- invalid service-tier values fail closed;
- user-visible publication rejects a 3.5/Lite semantic route.
