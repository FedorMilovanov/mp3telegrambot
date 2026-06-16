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

## 2026-06-16 — Playwright/DOM audit layer added

Added deterministic published-page audit tooling:

```bash
python tools/audit_telegraph_pages.py --limit 20
python tools/audit_telegraph_pages.py --requests-only --limit 20
python tools/audit_telegraph_pages.py --url https://telegra.ph/...
```

The tool prefers Playwright/Chromium when installed and falls back to `requests` on machines without browser binaries. It writes:

- `docs/telegraph_dom_audit.json` — machine-readable audit result;
- appended section in this history file.

Current checks catch visible production artifacts before they are missed by manual review:

- third-person wrappers (`Лектор показывает...`, `Автор объясняет...`);
- raw markdown/glue artifacts (`**`, `-* *`, `/ /`);
- suspicious source cards with invented Russian titles before original titles;
- empty paragraphs;
- broken-looking links;
- long Telegraph pages without visible navigation markers.

Also tightened the deterministic third-person scrubber for Reflection/Study prose: `Лектор показывает, как ...` now becomes direct prose instead of surviving as a warning on the published page.

## 2026-06-16 — Honest degraded mode for Gemini 3.5 failures

Policy decision: if primary Gemini 3.5 audio analysis is unavailable, do **not** publish synthetic Study/Reflection/Questions pages from weak fallback data.

Implemented behavior:

- default audio analysis remains `AUDIO_ANALYSIS_FALLBACK_MODE=strict`;
- when `gemini-3.5-flash` fails/quota-exhausts and root `ai_data` is missing, the pipeline enters honest degraded mode;
- AI-derived pages are skipped: no Synopsis, StudyAnalysis, Reflection, Terms, Questions, Quiz, Shorts/Clips/Montage candidates;
- the bot still sends the MP3;
- if YouTube captions are available, the caption includes grounded transcript timestamps only, marked as “from YouTube subtitles without AI summarization”;
- alternative RuTube/VK matching is skipped in degraded mode to avoid weak false-positive links from fallback titles.

This matches the quality rule: better no conspect than a shallow or hallucinated one.

## 2026-06-16 — Source-card title hallucination hardening

Live logs showed repeated `source_map_original_title` warnings such as:

- `• Умерщвление греха, Джон Оуэн (Of the Mortification of Sin, John Owen).`
- `• Все ради блага, Томас Уотсон (All Things for Good, Thomas Watson).`
- `• Пламенная проповедь, Мартин Ллойд-Джонс (Preaching and Preachers, Martyn Ллойд-Джонс).`

The old source normalizer misread these as `author, title` and could render the author as the bold title. It now recognizes the `RU-title, RU-author (Original-title, Original-author)` shape and renders verifiable title-first source cards:

- `• **Of the Mortification of Sin**, Джон Оуэн (John Owen).`
- `• **All Things for Good**, Томас Уотсон (Thomas Watson).`
- `• **Preaching and Preachers**, Мартин Ллойд-Джонс (Martyn Lloyd-Jones).`

Policy: if a Russian source title is not in the official registry, do not trust it; show the original title instead. Known official Russian titles (for example `Strange Fire` → `Чуждый огонь`) still render in Russian.

## 2026-06-16 — Strict surgical content-audit mode

Added configurable strict publication aborts for manual high-quality review sessions:

```env
CONTENT_AUDIT_MODE=strict
CONTENT_AUDIT_STRICT_CODES=all
```

or a surgical subset:

```env
CONTENT_AUDIT_MODE=strict
CONTENT_AUDIT_STRICT_CODES=third_person_warning,source_relevance_missing_warning,scripture_role_missing_warning
```

Default behavior is unchanged: only historically critical issues abort in strict mode. With `CONTENT_AUDIT_STRICT_CODES=all`, unresolved warnings such as thin scripture/source/application blocks stop publication instead of being merely logged.

Also cleaned duplicate empty-section guards in `telegraph_pages.py` and fixed malformed audit locations from `blocks[0` to `blocks[0]`, making logs easier to trace back to exact JSON blocks.

## 2026-06-16 — Page-audit strict publication gate

Page-level Telegraph audit can now block create/edit before hitting the Telegraph API.

```env
PAGE_AUDIT_MODE=strict
PAGE_AUDIT_STRICT_CODES=all
```

or targeted:

```env
PAGE_AUDIT_MODE=strict
PAGE_AUDIT_STRICT_CODES=source_map_original_title,third_person,mixed_greek_cyrillic
```

`PAGE_AUDIT_MODE` overrides `CONTENT_AUDIT_MODE`; if unset, it inherits `CONTENT_AUDIT_MODE`. Default remains warn-only.

This gives a second gate after section-level content audit: even if a formatting/source-card problem survives into final Telegraph nodes, createPage/editPage can abort instead of publishing a visually or bibliographically unsafe page.
