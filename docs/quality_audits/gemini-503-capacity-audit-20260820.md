# Gemini 503 capacity audit — 2026-08-20

## Executive conclusion

A client application cannot truthfully guarantee that Google will never return
HTTP 503. Google documents 503 as temporary backend overload/unavailability. The
correct production target is therefore **no application-induced retry storm,
bounded recovery, traffic smoothing, and an explicit higher-availability serving
class when the workload justifies it**.

The repository had multiple independent retry owners. In the worst paths a
single transient backend event could be multiplied by the number of configured
API keys, while several of those keys may belong to the same Google project.
That is structurally wrong because Gemini API rate limits are project-scoped,
not API-key-scoped.

This audit keeps the semantic quality contract unchanged: Gemini 3.7 Flash for
quality-sensitive work, HIGH thinking for the normal heavy path, no automatic
3.6/3.5/Lite semantic downgrade. Recovery may use an explicitly requested LOW
thinking pass only where the feature already defined that behavior.

## Root causes found in the repository

### P0 — retry multiplication by API-key count

Before this change:

- generic `core.globals.gemini_generate` could perform up to three transient
  attempts **per client**;
- Shorts Factory could perform two 503 attempts **per client per semantic pass**;
- audio analysis independently uploaded to Files API and retried inference
  across clients;
- several LiveDub owners called `generate_content` directly and implemented
  their own client loops.

With four configured keys, one backend overload could therefore create a burst
far larger than Google's recommended retry envelope. Google Cloud explicitly
recommends retrying a single event no more than two times and smoothing traffic
instead of sending sudden spikes.

### P0 — the supposed LOW recovery was actually HIGH

`core.globals._effective_thinking_level()` forced every Gemini 3.7 Flash request
to HIGH. Callers such as the audio timestamp/empty-output recovery explicitly
requested LOW, but the shared config silently promoted it to HIGH. Current
Gemini 3.7 Flash documentation supports LOW, MEDIUM and HIGH and rejects MINIMAL.
The production default remains HIGH; explicit LOW recovery is now preserved.

### P0 — API-key rotation was treated as capacity

Gemini API rate limits are measured per project, not per API key. Rotating four
keys from one project does not create four independent capacity pools. It can
instead turn one project/backend overload into a request storm.

The repaired code therefore shares one transient-attempt budget across key
rotation. Keys remain useful for independently configured projects/credentials,
but key count no longer multiplies one transient event.

### P1 — Files API and GenerateContent were conflated

Google's official Python SDK issue tracker contains a documented incident where
Files API uploads returned 503 across two independent projects while
`generateContent` remained operational. The repository now treats remote upload
and inference as separate failure domains with separate bounded budgets.

### P1 — hidden SDK retry ownership was not explicit everywhere

Factory clients already set `HttpRetryOptions(attempts=1)`, but generic clients
did not. The application now explicitly requests one SDK attempt where the
installed SDK exposes `HttpRetryOptions`; the application owns all higher-level
retry/rotation behavior. This prevents a future SDK default from silently
stacking retries under repository-owned retries.

### P1 — no process-wide smoothing for expensive requests

Independent bot features could enter Gemini concurrently. A process-wide
capacity gate now serializes expensive requests by default
(`GEMINI_HEAVY_MAX_CONCURRENCY=1`, configurable up to four) and publishes a
shared cooldown after overload so another coroutine cannot immediately hammer
the backend while the failing request sleeps.

## Implemented serving contract

1. **One transient event = at most three network attempts total**: the initial
   request plus a maximum of two retries. This limit is shared across API-key
   rotation and hard-capped at three.
2. **Exponential backoff + jitter**: default 15s, 30s, capped at 60s plus up to
   5s jitter. Values are configurable but the attempt count is never allowed
   above three.
3. **Shared heavy-request gate**: one concurrent expensive Gemini request per
   process by default. This smooths bursts from Factory, RUS, LiveDub and other
   semantic features.
4. **Shared cooldown after 503/5xx overload**: queued heavy calls respect the
   same cooldown instead of starting an independent retry stack.
5. **Separate Files and inference budgets**: remote audio upload outages cannot
   automatically fan out into repeated semantic inference and vice versa.
6. **No model downgrade**: Factory remains Gemini 3.7 Flash/HIGH/three-pass.
7. **LOW means LOW** on Gemini 3.7 when an existing bounded recovery step
   explicitly requests it. MINIMAL remains rejected for 3.7 and maps safely to
   the HIGH production default rather than being sent to the API.
8. **SDK retry stack disabled where supported** with
   `HttpRetryOptions(attempts=1)`.
9. **Truthful diagnostics**: 503 is backend availability, not proof that keys or
   quota are exhausted; 429 is the rate/quota class.

## Priority inference and Provisioned Throughput

The code does **not** silently enable a paid serving class. Existing
`GEMINI_SERVICE_TIER=priority` remains an explicit operator choice.

For eligible Tier 2/3 Gemini API projects, current GenerateContent Priority
Inference documentation states that Priority traffic receives higher reliability
and that traffic exceeding the Priority limit gracefully falls back to Standard
rather than failing simply because Priority capacity was exceeded. The current
Gemini 3.7 Flash model card lists Priority as a supported consumption option.

For workloads requiring reserved capacity/SLA-style guarantees rather than
best-effort shared capacity, Google Cloud's architectural answer is Vertex AI
Provisioned Throughput. That is a billing/platform decision and must not be
silently substituted by application code.

## Operational defaults

The following optional environment knobs are intentionally conservative:

```text
GEMINI_HEAVY_MAX_CONCURRENCY=1
GEMINI_TRANSIENT_MAX_ATTEMPTS=3
GEMINI_RETRY_BASE_SECONDS=15
GEMINI_RETRY_MAX_SECONDS=60
GEMINI_RETRY_JITTER_SECONDS=5
```

`GEMINI_TRANSIENT_MAX_ATTEMPTS` is hard-capped at 3 even if a larger value is
configured. Raising concurrency should be evidence-driven; more concurrency is
not a fix for 503.

## What this change cannot promise

- It cannot prevent a Google-side outage or overload from returning 503.
- It cannot turn several API keys in the same project into independent quotas.
- It cannot provide reserved capacity without a Google serving-tier/capacity
  product.
- It deliberately does not hide 503 by lowering the semantic model or accepting
  a weaker Factory result.

The measurable promise is narrower and testable: **the bot itself will not
amplify one transient failure into an unbounded or key-count-multiplied request
storm.**

## Primary-source review (45 sources)

Normative/current documentation was given priority. GitHub issues below are
listed separately as field evidence from the official Google SDK repository;
they are not treated as normative API guarantees.

### Gemini API — Google AI for Developers

1. https://ai.google.dev/gemini-api/docs/troubleshooting
2. https://ai.google.dev/gemini-api/docs/api-errors
3. https://ai.google.dev/gemini-api/docs/rate-limits
4. https://ai.google.dev/gemini-api/docs/models
5. https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash
6. https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash
7. https://ai.google.dev/gemini-api/docs/latest-model
8. https://ai.google.dev/gemini-api/docs/changelog
9. https://ai.google.dev/gemini-api/docs/deprecations
10. https://ai.google.dev/gemini-api/docs/thinking
11. https://ai.google.dev/gemini-api/docs/priority-inference
12. https://ai.google.dev/gemini-api/docs/generate-content/priority-inference
13. https://ai.google.dev/gemini-api/docs/optimization
14. https://ai.google.dev/gemini-api/docs/pricing
15. https://ai.google.dev/gemini-api/docs/file-input-methods
16. https://ai.google.dev/gemini-api/docs/files
17. https://ai.google.dev/gemini-api/docs/audio
18. https://ai.google.dev/gemini-api/docs/generate-content/audio
19. https://ai.google.dev/gemini-api/docs/generate-content/tokens
20. https://ai.google.dev/gemini-api/docs/long-context
21. https://ai.google.dev/gemini-api/docs/caching
22. https://ai.google.dev/gemini-api/docs/generate-content/caching
23. https://ai.google.dev/gemini-api/docs/structured-output
24. https://ai.google.dev/gemini-api/docs/generate-content/structured-output
25. https://ai.google.dev/gemini-api/docs/batch-api
26. https://ai.google.dev/gemini-api/docs/api-key
27. https://ai.google.dev/gemini-api/docs/openai
28. https://ai.google.dev/gemini-api/docs/migrate-to-interactions
29. https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026

### Google Cloud / Vertex AI

30. https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/api-errors
31. https://docs.cloud.google.com/vertex-ai/generative-ai/docs/resources/throughput-quota
32. https://cloud.google.com/vertex-ai/generative-ai/docs/error-code-429
33. https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/measure-provisioned-throughput
34. https://docs.cloud.google.com/vertex-ai/generative-ai/docs/purchase-provisioned-throughput
35. https://cloud.google.com/vertex-ai/generative-ai/docs/supported-models
36. https://cloud.google.com/blog/products/ai-machine-learning/reduce-429-errors-on-vertex-ai

### Official googleapis/python-genai source and release history

37. https://github.com/googleapis/python-genai/blob/main/google/genai/_api_client.py
38. https://github.com/googleapis/python-genai/blob/main/CHANGELOG.md

### Official SDK issue tracker — field evidence, not normative docs

39. https://github.com/googleapis/python-genai/issues/2489
40. https://github.com/googleapis/python-genai/issues/2448
41. https://github.com/googleapis/python-genai/issues/1373
42. https://github.com/googleapis/python-genai/issues/1893
43. https://github.com/googleapis/python-genai/issues/2506
44. https://github.com/googleapis/python-genai/issues/1288
45. https://github.com/googleapis/python-genai/issues/2522

## Merge gate

Do not merge on static reasoning alone. Required evidence:

- full Python 3.11 and 3.13 CI green;
- Windows runtime checks green;
- focused capacity tests prove 4 keys cannot expand one 503 event beyond three
  network calls;
- Factory quality/no-downgrade regressions green;
- source-owned architecture remains free of runtime monkey-patching;
- post-merge main CI green before production acceptance.
