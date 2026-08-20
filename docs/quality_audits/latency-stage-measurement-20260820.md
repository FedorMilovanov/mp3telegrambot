# Production latency stage measurement — 2026-08-20

## Why this exists

Gemini `503 UNAVAILABLE` occurred on the former Gemini 3.6 route as well as the current 3.7 route. Google documents 503 as temporary service overload/capacity exhaustion, so model migration alone cannot explain the multi-week reliability symptom. The application has also historically amplified capacity events through retries, client rotation and repeated preparation; that amplification is separately bounded by the shared capacity controller.

This audit adds measurement only. It does not change model choice, thinking level, prompts, retries, timeouts, service tier, media selectors, PO-token routing, Whisper, rendering quality or publication behavior.

## Measurements

One request-scoped trace is started by the source-owned video dispatcher and explicitly bound to that dispatcher `asyncio.Task` through a weak registry. The tracer deliberately does not use `ContextVar` or other inherited ambient request state; child tasks are not silently attributed to the parent trace.

The final log line reports total wall time and aggregates only these shared-owner stages when they execute in the owning request task:

- `gemini_inference_roundtrip`: time inside an awaited GenerateContent-like SDK call owned by the capacity controller;
- `gemini_files_roundtrip`: time inside an awaited Gemini Files API call;
- `gemini_*_semaphore_wait`: local wait for the process-wide heavy-Gemini slot;
- `gemini_*_cooldown_wait`: application-owned overload cooldown actually slept;
- `gemini_*_overload`, `*_circuit_trip`, `*_circuit_open`: event counts, not invented elapsed time;
- `process_yt_dlp`, `process_ffmpeg`, `process_ffprobe`, `process_node`, `process_deno`: elapsed time in source-owned external process trees;
- `process_other`: other commands routed through the same process owner.

The existing `gemini_runs` observability remains the source for model, thinking level, token usage, retry count, errors and per-Gemini-task duration. No duplicate telemetry database is added.

## Important limits

The production calls use non-streaming `generate_content`. Therefore `gemini_inference_roundtrip` truthfully measures request start to complete response, but it cannot split Google's internal queue / time-to-first-token / generation phases. Switching to streaming just for measurement would change production behavior and is intentionally rejected.

Because child tasks are not silently inherited, stage totals can intentionally under-account work that was spawned away from the owning dispatcher task. That is useful evidence rather than a reason to reintroduce ambient context: if total wall time is materially larger than measured stages, inspect the specific remaining owner and add one explicit timer there only if necessary.

Stage elapsed totals are diagnostic aggregates, not a critical-path decomposition; if future explicitly bound stages overlap concurrently, their sum may exceed wall time.

## Reading one real RUS + one Factory run

- If `gemini_inference_roundtrip` dominates while local semaphore/cooldown waits are small, provider/model inference latency dominates.
- If `gemini_*_semaphore_wait` dominates, local serialization or overlapping heavy tasks dominate.
- If `gemini_*_cooldown_wait` plus overload events dominate, provider 503s are being converted into deliberate application backoff; the next decision is whether to fail faster rather than wait longer.
- If `process_yt_dlp` dominates, diagnose YouTube/GVS/network rather than Gemini.
- If `process_ffmpeg` dominates, local CPU/media processing is the bottleneck.
- If total wall time is materially larger than all measured shared-owner stages, only then add a targeted timer to the remaining pipeline owner (for example Telegraph/Whisper/Telegram delivery). Do not build a general tracing platform first.

After the Node-only bgutil repair in #164, normal production YouTube work should not accumulate `process_deno` time.
