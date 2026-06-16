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

## 2026-06-16 — Live Telegraph link audit pass

Ran requests-based DOM audit over 33 Telegraph links from the provided runtime log.

After fixing a false positive from Telegraph's own chrome anchor without `href`, remaining findings were:

- `markdown_artifact`: 4 pages — visible raw `**...**` fragments inside prose/list text;
- `third_person_wrapper`: 2 pages — visible wrappers like `Лектор показывает...` / `Данкан подчеркивает...`;
- `source_map_original_title`: 2 pages — source cards still containing unverified Russian titles before original titles.

Follow-up fixes:

- DOM audit no longer treats Telegraph chrome anchors without `href` as bad links;
- third-person scrubber/page/DOM audit now recognizes common speaker surnames, not only generic `автор/лектор/проповедник` and `МакАртур`;
- official registry now preserves Calvin's `Institutes of the Christian Religion` as `Наставление в христианской вере`;
- existing already-published pages still need repair/re-publish if we want to rewrite their Telegraph content; the code changes prevent the same classes from passing silently in future strict runs.

## 2026-06-16 — Audit policy clarified: repair/log first, blocking opt-in only

Operator clarification: the goal is not to hide bad conspects; the goal is to find bugs, repair the generator, keep the pages available for reading/review, and record issues in history. Therefore strict audit modes now do **not** block publication by themselves.

Blocking requires an explicit extra opt-in:

```env
CONTENT_AUDIT_MODE=strict
CONTENT_AUDIT_STRICT_CODES=all
CONTENT_AUDIT_BLOCK_PUBLICATION=1

PAGE_AUDIT_MODE=strict
PAGE_AUDIT_STRICT_CODES=all
PAGE_AUDIT_BLOCK_PUBLICATION=1
```

Without `*_BLOCK_PUBLICATION=1`, strict mode is a loud audit mode: warnings are logged and can be written to history/repair reports, but the bot continues publishing so the operator can read and inspect the material.

Production recommendation for this workflow:

```env
CONTENT_AUDIT_MODE=warn
PAGE_AUDIT_MODE=warn
```

Then run DOM/repair audits over published links and fix generator bugs when a repeated class appears.

## 2026-06-16 — Repair path now fixes existing raw Markdown/source-card pages

Clarified workflow: pages remain available for reading and review; audit findings drive generator/repair fixes. The repair/postprocess path now handles already-published Telegraph nodes that contain raw Markdown markers in plain text, e.g. `• **Реформатское богословие**`, and re-parses them into proper Telegraph `<strong>` nodes.

Also strengthened source-card repair for existing pages where the invented Russian title is already wrapped in `<strong>`:

- before: `• Умерщвление греха, Джон Оуэн (Of the Mortification of Sin, John Owen)`
- after repair nodes: `• **Of the Mortification of Sin**, Джон Оуэн (John Owen)`

Confirmed locally via Telegraph `getPage` + current postprocess: the problematic sample pages change and page-audit warnings disappear. Actual `editPage` repair requires `TELEGRAPH_TOKEN` at runtime; the sandbox currently has no Telegraph token, so code is ready but remote pages were not edited from here.

## 2026-06-16 — Deterministic repair tool dry-run

Added `tools/repair_telegraph_pages.py` and a target list:

- `docs/telegraph_repair_targets_2026-06-16.md`

Dry-run over the 8 audited technical Telegraph pages:

```text
pages=8 changed=8 unresolved_after=0 errors=0 applied=0 mode=dry-run
```

This means the current deterministic repair/postprocess can clean all known issue classes from the target list without Gemini calls. Applying the repair on the runtime machine requires `TELEGRAPH_TOKEN`:

```bash
python tools/repair_telegraph_pages.py --url-file docs/telegraph_repair_targets_2026-06-16.md --apply
```

Also reduced a page-audit false positive: registered official Russian source titles (e.g. Calvin's `Наставление в христианской вере`) are no longer treated as hallucinated source-map titles.

## 2026-06-16 — Repair/audit operator UX follow-up

Small but useful follow-up for the repair workflow:

- `tools/audit_telegraph_pages.py` now supports `--url-file`, so the same target list can be used for dry-run repair and post-repair DOM audit.
- Added canonical registry support for Jonathan Edwards and `Religious Affections` → `Религиозные чувства`, so official Russian source titles are preserved while unregistered title guesses still fall back to original titles.

Typical loop on the runtime machine:

```bash
python tools/repair_telegraph_pages.py --url-file docs/telegraph_repair_targets_2026-06-16.md --apply
python tools/audit_telegraph_pages.py --url-file docs/telegraph_repair_targets_2026-06-16.md --requests-only
```

## 2026-06-16 — Repair tool hardening

Hardened repair tooling for runtime use:

- generated machine reports `docs/telegraph_dom_audit.json` and `docs/telegraph_repair_run.json` are now ignored by git; durable history stays in Markdown;
- `tools/repair_telegraph_pages.py --apply` now reports `ok=false` if `editPage` fails or `TELEGRAPH_TOKEN` is missing, instead of looking like a successful dry-run;
- added `--fail-on-unresolved` for CI/manual verification after a repair pass.

Confirmed dry-run on the 8 known technical pages still reaches:

```text
pages=8 changed=8 unresolved_after=0 errors=0 applied=0 mode=dry-run
```

## 2026-06-16 — Audit-generated repair targets

Added `--repair-targets-out` to `tools/audit_telegraph_pages.py`. This closes the operator loop:

```bash
python tools/audit_telegraph_pages.py \
  --archive docs/generated_pages_archive.md \
  --requests-only \
  --repair-targets-out docs/telegraph_repair_targets_AUTO.md

python tools/repair_telegraph_pages.py \
  --url-file docs/telegraph_repair_targets_AUTO.md \
  --apply \
  --fail-on-unresolved
```

The audit step now writes a Markdown list containing only pages with issues and annotates each URL with issue codes in an HTML comment.

## 2026-06-16 — Automatic post-publish Telegraph repair

Clarification implemented: repair is now part of the program flow, not only an operator CLI step.

After Telegraph pages are created and navigation links are edited, the main pipeline now runs one deterministic post-publish repair pass by default:

```env
TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH=1
```

It fetches the just-created Telegraph URLs, runs current postprocess/audit repair, and calls `editPage` if deterministic cleanup changed nodes. It does not call Gemini and it is non-fatal: failures are logged, not hidden.

Manual CLI/Telegram repair commands remain useful for old technical pages or historical batches, but new pages get the automatic safety pass immediately after publication.

## 2026-06-16 — Prompt/source policy alignment

Aligned Study prompt with deterministic repair policy:

- removed prompt examples that taught third-person wrappers like `Лоусон показывает...`;
- Study prompt now says to formulate the theological point directly, without describing author actions;
- unregistered Russian source titles are no longer presented as the positive example for Owen's `Of the Mortification of Sin`;
- prompt examples now prefer the original title unless the Russian title is known/registered.

This is the prompt-level counterpart to the deterministic repair rules: Gemini is now instructed to avoid the same source-card and third-person patterns that audit/repair catches later.

## 2026-06-16 — Auto-repair status persisted to archive

Post-publish auto-repair is now recorded in the durable generated-pages archive. After saving a generated page record, the pipeline updates repair metadata using the auto-repair results:

- `last_repaired_at`
- `repair_count`
- `last_repair_changed_pages`
- `last_repair_errors`

This makes automatic repair visible in archive Markdown/SQLite, not only in runtime logs.

## 2026-06-16 — Repair avoids unnecessary editPage calls

The deterministic repair service now skips `editPage` when current postprocess makes no node changes. This matters because auto-repair runs after every new Telegraph publication: clean pages should not spend extra Telegraph API quota or risk FLOOD_WAIT just to write identical content.

Changed behavior:

```text
postprocess changed nodes -> editPage
postprocess made no changes -> ok=True, changed=False, no editPage call
```

## 2026-06-16 — Auto-repair unresolved audits recorded

Auto-repair now records unresolved page-audit summaries as repair metadata, not only hard edit errors. If deterministic postprocess cannot fully clean a just-published page, the pipeline logs the unresolved audit and stores it in the generated-pages archive repair fields.

This keeps the normal behavior (pages remain available) while making recurring unfixable defects visible in archive/history for the next generator/prompt repair pass.

## 2026-06-16 — Multi-part Telegraph repair coverage

Auto-repair and archive repair now expand Telegraph part chains via `➡ Дальше` links before repairing. This closes a gap for multi-part Synopsis pages: previously only the first Telegraph URL stored in the archive was repaired, while part 2/3/4/5 could keep old formatting artifacts.

Behavior now:

```text
stored first-page URL -> fetch content -> find ➡ Дальше links -> repair every chained Telegraph part
```

The repair remains deterministic and Gemini-free.

## 2026-06-16 — CLI audit/repair expands multi-part chains by default

The standalone CLI tools now match pipeline behavior for multi-part Telegraph pages:

- `tools/repair_telegraph_pages.py` follows `➡ Дальше` chains by default before repairing;
- `tools/audit_telegraph_pages.py` also expands chains by default before DOM audit;
- both tools support `--no-expand-chains` for one-page debugging.

Verified on `Vernost-v-uchenichestve--Mark-Dever-06-16`: a single first-part URL expands to 5 Telegraph pages in repair dry-run.

## 2026-06-16 — Gemini content-audit retry for expanded pages

Added a real Gemini-level repair pass before Study/Reflection publication, not just deterministic cleanup.

When section-level content audit finds unresolved warnings (thin scripture role, missing source relevance, third-person wrappers, thin application/lexicon), the expanded-page runner now performs one targeted retry:

```env
EXPANDED_CONTENT_AUDIT_RETRY=1
```

The retry prompt sends:

- exact audit issues;
- original task context;
- current `{outline, sections}` JSON.

Gemini must return repaired `{outline, sections}`. The retry is accepted only if audit warning count does not increase. It uses the configured primary model only (`allow_model_fallback=False`) to avoid low-quality fallback rewrites.

This is the generator-quality counterpart to postprocess repair: the model is asked to fix the actual weak section before Telegraph publication.

## 2026-06-16 — Manual prompt pass: source/guardrail wording

Manual pass over the Study prompt removed wording that could teach the model the very defects repair/audit later catches:

- removed explicit third-person positive examples from Study prompt;
- removed `ПОЗИЦИЯ КАНАЛА` heading wording from the public prompt body, replacing it with internal guardrail language;
- replaced `В работе X автор Y...` / `Y в «X»...` source guidance with source-card relevance wording that does not train author-action wrappers;
- corrected source-card examples to title-first with original title when the Russian title is not registry-confirmed;
- fixed the `Safe in the Arms of God` example to title-first.

This reduces prompt-induced leaks and source-card hallucinations before deterministic repair has to intervene.

## 2026-06-16 — Literal bad-pattern prompt cleanup

Manual prompt pass removed literal repeated bad examples from the prompt text itself:

- replaced explicit third-person bad phrases (`МакАртур показывает`, `автор подчеркивает`, etc.) with pattern-level descriptions;
- removed remaining literal `позиция канала` wording from Study prompt;
- kept positive direct-style examples.

This reduces the chance that Gemini copies a forbidden phrase from the prompt while still preserving the instruction's intent.

## 2026-06-16 — Prompt health now tracks leaky literals

Prompt health now detects exact literal phrases that previously leaked into generated pages or taught bad patterns, such as:

- `позиция канала`
- `В работе X автор Y`
- `Русский Автор, *«Русское название»*`
- `Лоусон показывает`

`/prompthealth` now reports a `leaks=` counter per prompt. Current main prompts are covered by a regression test requiring zero known leaky literals. This prevents future prompt edits from reintroducing the same literal bad examples we just removed.
