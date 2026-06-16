# Quality audit history

## 2026-06-16 — Gemini 3.5 / Shepherds' Conference batch

Source: operator runtime log excerpt, 10:42–15:34 Europe/Moscow.

### Observed production issues

1. **Primary Gemini 3.5 instability/quota pressure**
   - Many `503 UNAVAILABLE / high demand` retries on `gemini-3.5-flash`.
   - Later many `429 RESOURCE_EXHAUSTED` across all configured keys for `gemini-3.5-flash`.
   - Before this audit, text/page generation kept retrying the exhausted primary model on every page, wasting time before falling back.

2. **Lite-model fallback produced unsafe root analysis**
   - Several audio-analysis fallbacks to `gemini-2.5-flash-lite` ended with `FinishReason.MAX_TOKENS` and 130k–220k-character partial JSON.
   - The root `ai_data` became `None`, but the pipeline still generated StudyAnalysis/Reflection from mostly empty inputs and fallback titles.
   - Result: partial publication, weak pages, missing synopsis, captions with `format=other`, `ai_data=no`.

3. **Partial publication should not imply a full AI analysis**
   - Examples in logs: GS2, GS6, GS8, GS9, GS10, GS13, GS15 produced `ai_data=no` yet still created some derived pages.
   - This is misleading: when root analysis failed, downstream theological pages do not have enough grounded material.

4. **Synopsis failures are mostly quota/overload, not Telegraph failures**
   - Many `Synopsis v2 error: 429/503` events after transcript acquisition.
   - Several items became `publication_status=partial missing=synopsis` while Study/Reflection still published.

5. **Short RuTube false-positive risk**
   - For a 2-minute clip, RuTube listing returned a weak score around `0.51` and accepted an unrelated short result.
   - Short durations make duration matching too permissive; textual confidence must be higher.

6. **Content quality warnings to keep tracking**
   - Repeated `scripture_role_missing_warning`: scripture cards without role/explanation.
   - Repeated `source_relevance_missing_warning`: sources without why-relevant.
   - Repeated `source_map_original_title`: invented Russian source title before original title.
   - Occasional `third_person_warning`: e.g. “Лектор показывает...” remained on Reflection page.
   - Occasional prompt-context leak scrubbed after generation; prompts should keep reducing this.

### Surgical fixes applied after this audit

1. Audio analysis is now **quality-first by default**:
   - default `AUDIO_ANALYSIS_FALLBACK_MODE=strict` tries only configured primary model, normally `gemini-3.5-flash`;
   - emergency lite fallback remains available with `AUDIO_ANALYSIS_FALLBACK_MODE=lite`.

2. Audio/text generation now marks a model as in-memory exhausted after all keys hit quota:
   - avoids repeatedly burning time on known-exhausted `gemini-3.5-flash` during the same process.

3. If root audio analysis is missing/invalid, the pipeline now skips AI-derived Telegraph pages:
   - no StudyAnalysis/Reflection/Terms/Questions pages from empty `ai_data`;
   - prevents hallucinated partial publications after MAX_TOKENS or total quota failure.

4. RuTube weak-match threshold is stricter for short videos:
   - videos under 5 minutes require `score >= 0.75` for weak listing return.

5. Added regression tests:
   - default audio fallback is primary-only;
   - quota exhaustion is marked;
   - AI pages are skipped when root analysis is missing;
   - short RuTube matching uses stricter threshold.

### Operational recommendation

For best theological quality keep:

```env
GEMINI_MODEL=gemini-3.5-flash
AUDIO_ANALYSIS_FALLBACK_MODE=strict
```

Use `AUDIO_ANALYSIS_FALLBACK_MODE=lite` only when degraded emergency output is acceptable.

If many 429s appear, pause batch processing until quota resets or use paid/quota-raised projects. Continuing a large playlist under 429 pressure produces partial archives and wastes retry time.
