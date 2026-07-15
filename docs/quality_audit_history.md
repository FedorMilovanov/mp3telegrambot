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

## 2026-06-16 — Prompt leaky-literal sweep expanded

Extended prompt-health leaky literal guard and removed additional prompt phrases that can be copied by Gemini despite being negative examples:

- `Джон МакАртур анализирует`
- `проповедник показывает`
- `Чередуй полное имя`
- invented-source examples such as `Спасение младенцев`, `Младенцы во славе`, `Странный огонь`

The prompts now describe these as patterns rather than repeating exact bad output strings. Current prompt-health check reports zero known leaky literals across main prompts and the deep audio prompt sample.

## 2026-06-16 — Channel-position literal cleanup completed

Removed remaining Study prompt phrases that could leak editorial/channel framing into public pages:

- `позицию канала`
- `наш канал`
- `редакции/каналу`
- `нерв позиции канала`

Replaced them with neutral internal-instruction wording (`внутренняя редакционная рамка`, `внутренняя инструкция`, `богословский guardrail`). Prompt-health leaky literal list was expanded to guard against reintroducing these phrases.

## 2026-06-16 — Lexicon/source prompt wrapper cleanup

Manual prompt pass removed remaining Study lexicon/source guidance that encouraged author-action wrappers:

- `Используй имя проповедника...`
- `Вошер разворачивает...`
- `Эдвардс настаивает...`
- `МакАртур цитирует...`
- `X полезна здесь потому, что показывает...`

Replacement guidance now asks for direct descriptions of the sermon moment and the term/source function without starting from the author's name. This should reduce third-person wrappers in lexicon/source blocks before content-audit retry or deterministic repair need to intervene.

## 2026-06-16 — Prompt meta-formula literal cleanup

Manual prompt pass removed additional literal meta-formulas from prompts and shared prompt rules:

- `в материале говорится/рассматривается`
- `материал показывает/касается`
- `следует отметить/следует заметить/можно сказать`
- `данный раздел/этот блок/в этой секции`
- `автор показывает`

These are now described as pattern classes (`канцелярские вводные`, `мета-формулы про материал/раздел`) instead of being repeated as exact phrases Gemini may copy. Prompt-health leaky literal detection was expanded accordingly and current prompts report zero known leaks.

## 2026-06-16 — Lexicon prompt false-precision literal cleanup

Manual prompt pass removed exact false-precision examples that could be copied into generated lexicon/source blocks:

- `TDNT, том 3, с. 456`
- `BDAG, с.456`
- `BDAG даёт дословно`
- bad Russian case examples after `согласно`

The prompt now describes the error as a pattern: do not invent dictionary volumes/pages; use cautious lexical wording unless absolutely certain; after `согласно` use dative case. Prompt-health leaky literal detection was expanded to prevent these exact bad examples from returning to live prompts.

## 2026-06-16 — Source registry expanded for source-pack authors

Expanded deterministic source-card registry for common authors appearing in `core/source_packs.py`, including Murray, Warfield, Ryle, Berkhof, Bridges, Brooks, Bavinck, Hoekema, Horton, Chapell, Goldsworthy, Greidanus, Machen and others.

Added known official Russian titles for several high-frequency works:

- `Redemption Accomplished and Applied` → `Искупление совершённое и применённое`
- `Holiness` → `Святость`
- `The Pursuit of Holiness` → `Стремление к святости`
- `The Doctrine of Repentance` → `Учение о покаянии`

This reduces English-author leakage in source cards while preserving original titles when Russian titles are not registry-confirmed.

## 2026-06-16 — Source-pack surname aliases covered

Added deterministic surname aliases for authors that appear in `core/source_packs.py` with short labels (for example `Owen — ...`, `Warfield — ...`, `Dever — ...`). Source-card rendering now converts those pack labels to Russian display names while keeping full original author names in parenthetical verifiers.

Examples:

- `Owen, Mortification of Sin` → `**Mortification of Sin**, Джон Оуэн (John Owen)`
- `Warfield, Inspiration and Authority of the Bible` → `**Inspiration and Authority of the Bible**, Б. Б. Уорфилд (B.B. Warfield)`

Added a regression test that scans `core/source_packs.py` and fails if an English source-pack author label lacks a source-card registry alias.

## 2026-06-16 — Tim Keller removed from source recommendations

Per operator instruction, Tim Keller is no longer recommended as a source.

Changes:

- removed Tim Keller from Study prompt authorized/recommended source lists;
- added a deterministic source-card denylist for Tim Keller / Timothy Keller / Keller / Тим Келлер;
- source-card normalization now silently drops Keller source cards instead of rendering or repairing them;
- structured source blocks with Keller are skipped during Telegraph rendering;
- prompt-health leaky literal guard includes Keller names so they do not re-enter prompts.

This is intentionally silent in generated pages: no warning card, no replacement source invented.

## 2026-06-17 — Recovery for outdated first part without next-link

The new run exposed a specific multi-part Synopsis edge case: part 1 `editPage` can fail with `CONTENT_TOO_BIG` before the `➡ Дальше` pagination link is written. Parts `-2`, `-3`, ... still exist, but a chain walk starting from part 1 cannot discover them.

Fixes:

- future Synopsis edit now retries part 1 without TOC when TOC makes `editPage` too large, preserving the `➡ Дальше` link;
- audit/repair chain expansion now probes the conventional `-2` URL when the first page has no next-link, so old pages with this failure are still recoverable;
- DOM audit now treats `Назад` / `Дальше` pagination links as valid navigation.

Verified against the latest run: auditing only `https://telegra.ph/SHest-dnej-tvoreniya-vselennoj--Dzhon-MakArtur-06-17` expands to all 4 parts and reports zero DOM issues.

## 2026-06-17 — Latest run audited and repaired

Audited the latest `Шесть дней творения вселенной` run from the operator log.

Findings:

- root audio analysis used strict `gemini-3.5-flash` only;
- timestamp coverage repair succeeded (`36 lines`);
- Study content-audit retry succeeded (`14 -> 0` warnings);
- Synopsis part 1 had previously missed the `➡ Дальше` link because TOC made `editPage` too large;
- Study page still had a deterministic style artifact: `Данный академический труд...` and split hyphenated term rendering (`Историко — грамматический`).

Actions applied with the provided Telegraph token:

- deterministic repair applied to the latest pages;
- Study page was edited (`changed=1`);
- Synopsis part 1 was edited to add the missing `➡ Дальше: 2/4` pagination link before cross-page links;
- post-repair DOM audit over the full 4-part Synopsis chain reports `pages_with_issues=0`.

Code hardening from this pass:

- postprocess now repairs split hyphenated terms such as `Историко — грамматический` -> `Историко-грамматический` even when the first word is inside `<strong>`;
- content/postprocess now rewrites `Данный академический труд` to `Академический труд`.

## 2026-06-17 — Synopsis density retry accepts issue reduction

The latest run showed `Synopsis v2: density retry rejected — not denser than original` after a retry intended to fix `synopsis_too_few_paragraphs`. The old acceptance rule compared only a coarse density score (chars + section count + small coverage bonus). A retry that reduced audit problems but did not raise the score enough could be rejected.

Updated acceptance rule:

```text
accept retry if density_score improves OR synopsis_quality_issue_count decreases
```

The log now records both score and issue-count deltas. This better matches the goal: if Gemini fixes the concrete density/audit problem, keep the improved version even when total character score is similar.

## 2026-06-17 — Density retry acceptance made safer

The previous density retry improvement accepted a retry when the issue count decreased, even if the coarse density score was lower. Tightened this so issue-count improvement is accepted only when the retry is not substantially thinner:

```text
accept if score improves OR (issue_count decreases AND new_score >= 85% of old_score)
```

This keeps the benefit of accepting concrete audit improvements while preventing a shorter/shallower retry from replacing a fuller Synopsis just because one warning disappeared.

## 2026-06-17 — Mixed English `day` in Scripture quotes

The latest run exposed a small mixed-language artifact in a generated Scripture quote during the first audio analysis pass:

```text
И был вечер, и было утро: day один
```

Added a narrow deterministic typo repair for `day один/первый` -> `день один/первый`. The final published pages did not retain this artifact after Study content-audit retry, but the normalizer now catches the class earlier if it appears again.

## 2026-06-17 — Quiz/test generator hardened

Reviewed and upgraded `services/quiz_generator.py` (Telegram native quiz/test questions):

- structured JSON schema for quiz output;
- strict validation: exactly 4 unique options, valid `correct` index, non-empty explanation;
- duplicate question removal;
- safer trimming for Telegram limits;
- richer grounded context from `main_topic`, `analysis_summary`, `argument_arc`, timestamps, key categories and terms_data;
- high thinking level on the configured primary model;
- Gemini observability logging for quiz generation.

This makes the optional quiz/test feature less likely to produce shallow, duplicated or invalid polls.

## 2026-06-17 — Quiz prompt included in prompt-health leak checks

Follow-up hardening for the quiz/test generator:

- `QUIZ_QUESTION_COUNT` parsing is now robust against invalid environment values;
- quiz prompt no longer contains the literal bad phrase `автор показывает`; it uses a pattern-level description instead;
- `/prompthealth` now includes `QUIZ_PROMPT`, so leaky literal regression checks cover quiz generation too.

## 2026-06-17 — Quiz parser accepts common Gemini variants safely

Hardened quiz/test parsing further:

- accepts wrapped JSON objects such as `{ "questions": [...] }`;
- accepts correct answers as numeric index, letter (`A`/`B`/`C`/`D`) or exact option text;
- still rejects invalid correct answers;
- rejects weak quiz options like `all of the above` / `none of the above` / `все перечисленное` / `нет правильного ответа`.

This makes the quiz feature more tolerant of harmless Gemini output-shape variation while still refusing low-quality poll patterns.

## 2026-06-17 — Quiz correct-answer parser hardened

Quiz parser now accepts more safe Gemini variants for the correct answer field:

- numeric index strings;
- Latin letters `A/B/C/D`;
- Cyrillic letters `А/Б/В/Г`;
- labels like `вариант Г` / `option C`;
- exact option text.

It still rejects invalid or ambiguous answers and keeps the four-unique-options rule.

## 2026-06-17 — Remaining Keller/name and retry-prompt literals removed

Follow-up sweep removed remaining non-source references that could confuse the source policy:

- removed Keller normalization entries outside the explicit source-card denylist;
- removed the literal `автор показывает` from the content-audit retry prompt, replacing it with a pattern-level description.

The explicit Keller denylist remains in `core/source_titles.py` so any source-card attempt is still silently dropped.

## 2026-06-17 — Reflection question normalization

Improved the questions/reflection pipeline beyond quiz polls:

- generated reflection questions are normalized before legacy Questions pages, combined Study+Reflection prompts, and ReflectionApplication prompts;
- missing question marks are repaired only for question-like Russian starts (`как/почему/что/...`);
- duplicate questions are removed after normalization;
- declarative slogans and too-short entries are filtered;
- 🟢/🔵 marker contract is preserved, defaulting to 🟢 when Gemini omits a marker.

This prevents weak duplicated/generated questions from becoming the scaffold of a Reflection page.

## 2026-06-17 — Shared question quality helpers

Added shared deterministic helpers for generated questions used by both Quiz polls and Reflection/Questions pages:

- repair missing `?` for question-like starts;
- reject generic questions such as `Как это применить?` / `Что это значит для меня?`;
- normalize question keys for dedupe;
- preserve question mark when trimming long questions.

`services.quiz_generator` and `services.telegraph_pages` now share the same question usability logic, reducing drift between Telegram quiz polls and Telegraph reflection questions.

## 2026-06-18 — Quiz/test guardrails tightened after parser audit

Follow-up audit of Telegram Quiz/test generation found two risky edges and closed them:

- numeric-string answers are now treated conservatively: JSON integer `1` still means 0-based index as requested by schema, while string `"1"` is treated as a human/list-style first answer instead of silently shifting to option 2;
- meta/generic quiz questions such as `Что утверждает материал?` and `Какой ответ верен?` are rejected by the shared question-quality layer;
- placeholder/weak quiz options (`A/B/C/D`, `1/2/3/4`, yes/no, `не знаю`, `затрудняюсь`, etc.) are rejected before polls are accepted;
- send-time Telegram poll payloads are sanitized again so even non-parser callers cannot send overlong questions/explanations, invalid option lists, or bad correct indexes.

This keeps the quiz feature in the “repair/filter bad Gemini shape, publish only usable tests” lane without hiding broader conspect output.

## 2026-06-18 — Quiz prompt final self-check added

Static prompt-health review showed `QUIZ_PROMPT` had no final self-check block while the long Telegraph prompts did. Added a compact final self-check directly to the quiz prompt:

- exact requested question count;
- exactly four meaningful options per poll;
- unambiguous correct answer grounded in the material;
- no placeholder distractors;
- no meta-question about choosing an “answer/variant”;
- explanation remains within Telegram limits and does not add outside facts.

`/prompthealth` now reports `QUIZ_PROMPT.final_checks=1` with zero known leaky literals.

## 2026-06-18 — Quiz pedagogy upgraded: close answers, non-trivial distractors

User requested that Telegram Quiz/tests stop being easy “guess the obvious” polls. The quiz generator now has both prompt-level and deterministic guardrails for smarter tests:

- prompt now requires all four options to be close in semantic category, length, and specificity;
- distractors must be plausible near-alternatives, not absurd negations or caricatures;
- simple recognition questions are discouraged unless tied to the argument's interpretation;
- parser rejects options that are too short/thin, placeholder-like, length-outliers, or obvious negations such as “not important / not related / cancels exegesis” patterns;
- parser rejects option sets that look category-mismatched rather than like four near answers;
- send-time poll sanitizer uses the same quality check, so invalid external/non-parser quiz payloads are skipped;
- if Gemini returns too few quality-accepted questions, generation performs one quality retry with explicit instruction to create closer, more thoughtful answers.

This keeps the user-facing quiz closer to a real theological comprehension test: one correct answer, but the wrong answers are plausible enough that the learner must understand the argument.

## 2026-06-18 — Bot admin-message HTML safety bugs fixed

Bug audit found runtime Telegram HTML risks in admin/archive commands:

- `/archive`-style output used to truncate already-built HTML at an arbitrary byte/character boundary, which could cut an `<a>`/`<b>`/`<code>` tag and make Telegram reject the message with an entity parse error;
- `<pre>` admin outputs escaped first and then truncated, which could cut an escaped entity such as `&lt;` / `&amp;` and also cause Telegram parse failures;
- `/resetcache` echoed user-supplied `video_id` inside `<code>` without HTML escaping when the argument was not a parsed YouTube id.

Fixes:

- archive formatter now clips plain fields before escaping and truncates at record boundaries;
- added `_html_pre_message()` that trims plain text before HTML escaping, so entities cannot be cut;
- repair/segment admin outputs now use the safe pre-message helper;
- resetcache echo now escapes the displayed id.

Regression tests cover long archive output, long preformatted output with `<`, `>`, `&`, and resetcache escaping.

## 2026-06-18 — Agent contract and regression instructions added

Repository had no root agent/maintenance contract for future AI/code agents. Added `AGENTS.md` covering:

- repair/log/history-first quality policy;
- explicit opt-in publication blocking flags only;
- Telegraph token implies programmatic repair rather than manual workflow;
- required `python tools/verify_repo.py` verification;
- prompt-health/leaky-literal policy;
- source-card/Keller policy;
- Telegram HTML safety rules;
- quiz/test quality rules;
- git/secret hygiene.

Added regression tests to ensure the contract remains present and includes the important guardrails. Also added a command-registration regression test so new async command handlers cannot be forgotten in `main.py`.

## 2026-06-18 — Segment pagination callback HTML truncation fixed

Follow-up Telegram HTML audit found the same escaped-then-truncated pattern in the `segpage:` callback branch. That could cut escaped entities in paginated segment text and make Telegram reject `edit_message_text`.

The callback now reuses the safe `_html_pre_message()` helper from command handlers, so plain text is trimmed before escaping and wrapped in `<pre>`. Regression test now checks the callback uses the helper and no longer contains the unsafe `safe[:3850]` truncation pattern.

## 2026-06-18 — Archive quality admin HTML escaping fixed

Continued Telegram/admin-output audit found that archive quality readouts could interpolate archive-derived strings directly into HTML:

- prompt variant names;
- author/title/status values;
- warning kinds;
- Telegraph URLs inside `<a href="...">`.

If a malformed archive record contained `<`, `>`, or `&`, admin commands such as `/archivequality`, `/qualityrecords`, `/comparevariants`, and `/promptrecommend` could produce invalid Telegram HTML or render unintended markup.

Fixes:

- `core.archive_quality` now HTML-escapes all archive-derived fields and quote-escapes link hrefs;
- admin report sending now passes through `_html_message_limit()` so long HTML reports are not cut through tags/entities;
- regression tests cover malicious archive fields, unsafe URLs, and tag/entity-safe truncation.

## 2026-06-18 — Metrics/status admin HTML escaping and truncation hardened

Continued surgical Telegram HTML audit found remaining admin readouts that could still render archive/log/env-derived strings without escaping or could rely on unsafe internal truncation:

- Gemini metrics report used task/model/error/finish-reason/rejection strings from the observability DB inside HTML without escaping;
- metrics report also had its own raw `text[:3820]` truncation, which could cut an HTML tag/entity before the command handler saw it;
- `/status` and `/disk` had dynamic environment/path/exception values in HTML messages.

Fixes:

- `core.observability.format_gemini_metrics_report()` now escapes all DB-derived display fields and no longer performs raw HTML slicing;
- admin command handlers send long HTML reports through `_html_message_limit()`;
- `/status` escapes Bot API URL, backup suffix and cache fingerprint values;
- `/disk` escapes exception text;
- regression tests cover malicious metrics rows and safe status/disk command formatting.

## 2026-07-05 — AUDIT R4: тотальный аудит всех слоёв (55 находок, 6 параллельных ревью)

Полный хирургический аудит ~38 500 строк: entry points/инфраструктура, Telegram-хендлеры, пайплайны, AI/текст-слой, Telegraph-публикация, медиа-сервисы. Плюс базовая гигиена: на HEAD падало 5 тестов (3 устаревших после намеренных фиксов, 1 мёртвая проверка аудита, 1 формат callback-данных).

Ключевые исправленные классы дефектов:

- **Мёртвые фичи**: ID3-главы mp3 (isinstance-гейт против строковых timestamps), trim-кнопки Shorts (в записи хранился удаляемый клип вместо исходника), `/pdf` (тело-заглушка `...`), проверка `source_map_original_title` (BUG-7 сделал её недостижимой), `options_not_close` в квизах (недостижимый порог — удалена честно), NO_PROXY-маскировка секретов в логах (фильтр root-логгера не видит записи дочерних), npx-fallback vot-cli (argv[0] со встроенным пробелом).
- **Порча контента**: git-скраб вырезал обычные английские слова из цитат Писания (Commit/Branch/commit adultery); английские фразы переписывались в фейковые source-карточки; денилист Келлера обнулял целые поля прозы и убивал карточки W. Phillip Keller; полные названия книг («Иоанна 3:16») линкифицировались как таймкоды видео; `[N/M]`-навигация теряла скобки.
- **Потеря данных/результатов**: успешный ответ Gemini второго круга затирался следующей итерацией цикла моделей; upsert архива затирал сохранённые URL пустыми; экспорт публичного архива терял вторую страницу «Разбор»; CONTENT_TOO_BIG глубже 2 уровней молча терял четверть контента; ENG-перевод терялся при «файл слишком большой»; montage/shorts удаляли одолженное LiveDub-видео (highlights/clips падали на английский оригинал).
- **Жизненный цикл/конкурентность**: rate-limit asyncio-локи переживали пересоздание event loop (все не-VIP запросы падали после авторестарта); SIGTERM/​/stop не работали в health-check-режиме; отмена LiveDub-задачи оставляла vot-cli/ffmpeg/Whisper сиротами; PDF-генератор глобально патчил subprocess.Popen и мог убить чужой ffmpeg; кэш прогресса смешивал чаты.
- **Лимиты Telegram/Telegraph**: caption-префиксы поверх уже обрезанных 1024; составные заголовки >256; отсутствие FLOOD_WAIT-ретрая в `_telegraph_post`; сдвиг индекса правильного ответа квиза после dedupe.
- **Кэш/лимиты**: кэш-hit перекачивал аудио при наличии `_64.mp3`; таймаут ожидания per-video лока сжигал слот дневного лимита без рефанда; точный поиск по video_id сканировал только 500 последних записей архива.

Регрессионные тесты: `tests/test_v3_audit_r4_{infra,ai_layer,handlers,media,pipelines,telegraph}.py` (~60 проверок). `.env.example` синхронизирован с кодом (5 мёртвых переменных удалены, ~25 реальных задокументированы с проверенными дефолтами), добавлена зависимость tzdata для сброса лимитов по Москве на Windows.

## 2026-07-05 — AUDIT R5: конспект-пайплайн, промпты и выравнивание под Gemini 3.5 Flash

Глубокий аудит формирования конспектов: карта всех промптов (инвентарь, размеры, потребители), сверка с онлайн-документацией Gemini API (июль 2026) и хирургические улучшения. Ключевое:

- **Срочная миграция моделей**: обе fallback-модели умирают в июле 2026 (3.1-flash-lite-preview — 09.07, 2.5-flash-lite — ~22.07). Все цепочки переведены на GA gemini-3.1-flash-lite; requirements: google-genai floor ≥2.0 (thinking_level API), yt-dlp ≥2026.7.4 (CVE-2026-55404).
- **Thinking-overflow retry**: на Gemini API thinking-токены делят бюджет с max_output_tokens; MAX_TOKENS/пустой ответ означал полную потерю аудио-анализа. Теперь один повтор с thinking_level=low. Лог оценки аудио-токенов (32 ток/сек) для диагностики TPM.
- **Грунтовка Study/Reflection стенограммой**: text-only вызовы просили verbatim-цитаты у модели без текста речи (главный источник выдуманных цитат). Конспект теперь передаёт фрагменты дословной стенограммы (до 24К знаков) через приватный ключ ai_data (в кэш не персистится).
- **STUDY-промпт −15%**: 12К-знаковый статический каталог авторов заменён компактным ядром + тематическим {source_pack}; мёртвый AUTHORS_REFERENCE удалён; убраны дубли блоков; правила длины — единый источник (профиль длительности).
- **Контракты полей блоков** перенесены в промпты, чей вывод аудитится: scripture (role_in_argument+common_misreading), source (why_relevant), lexicon — в STUDY; application (challenge+anchor_timestamp+concrete_step) — в REFLECTION. Раньше требования были только в мёртвом V2-промпте конспекта.
- **Самопротиворечия форматов**: примеры [⏱ M:SS] в REFLECTION (промпт сам учил запрещённым скобкам), ⚠️ vs ❌ в TYPE 6, TYPE-6/STUDY-блоки в конспект-промптах, двойные фигурные скобки в last-resort simple_prompt (модели показывали невалидный JSON), правило порядка inline-таймкодов добавлено в дефолтный VERBATIM-промпт.
- **Дженерализация именованных примеров** (Вошер/Мюллер/МакАртур → паттерн-формулировки) по AGENTS.md; /prompthealth теперь мониторит и SYNOPSIS_VERBATIM_PROMPT (дефолтный) — ранее только V2; квиз-промпт синхронизирован с детерминированным валидатором (двухсловные варианты ≥8 знаков, сопоставимая длина).

Регрессионные тесты: tests/test_v3_audit_r5_prompts.py.

## 2026-07-05 — AUDIT R6: дочистка (DRY правил, самосоздаваемые обёртки, экономия вывода)

- content_audit больше не переписывает «В материале говорится/Материал показывает» в запрещённые обёртки «Автор говорит/показывает» (которые следом вычищал другой скраббер) — сразу безличные формы (Говорится/Показывается/Критикуется…).
- DRY завершён: ручные копии правил inline-таймкодов в STUDY/REFLECTION и QA-правила таймкодов заменены плейсхолдерами {INLINE_TIMESTAMP_RULES}/{QA_TIMESTAMP_RULE}; общее правило обогащено (запрет «таймкод — тире» и оба ❌-примера). prompt_rules.py снова единственный источник истины.
- Экономия output-токенов: рендерер игнорирует content при валидных blocks — STUDY/REFLECTION теперь просят в content только короткий дайджест (2–3 предложения) вместо полного дубля (на very_long это удваивало вывод и провоцировало MAX_TOKENS).
- Схема квиза: minItems/maxItems=4 для options на уровне response_schema (валидатор молча выбрасывал вопросы с 3/5 вариантами).
- Reflection: thinking_level=high (override medium жил ниже окна проверки регрессионного теста V3-P15/17; окно расширено).

Тесты: tests/test_v3_audit_r6_polish.py.

> Примечание: раунды R7–R48 велись напрямую по коммитам (детальная история —
> в git log), в этот журнал не переносились. R49 возобновляет запись здесь.

## 2026-07-14 — AUDIT R49: свежие Конспекты/Разборы по Исаии 53 (дампы 07-14)

Оператор запушил 12 дампов (4 проповеди по Ис. 53 × Конспект/Разбор/Размышление)
с просьбой «внимательно изучить по всем стандартам». Дефекты дословного режима
чтения главы целиком и связанные — детерминированные починки:

- **Ссылка на Писание в косвенном падеже → видео-таймкод**: «В книге Деяний 2:23»,
  «стих 8:32», «Исаии 6:1» превращались в кликабельный таймкод видео. Guard по
  названию книги промахивался на склонениях и оборотах «в книге/главе/стихе».
  Добавлен `_SCRIPTURE_CTX_BEFORE` (converters/md_telegraph.py): число НЕ таймкод,
  если ему предшествует склонённое название книги или книго-цитирующий оборот.
- **Голый ⏱-таймкод внутри дословной цитаты «…»**: в чтении Ис. 53 метки видео
  падали в середину стиха («…ни во что ставили Его ⏱ 2:18. Но Он взял…»). Цитата
  занимает несколько абзацев, поэтому per-node/regex её span не видит.
  `_strip_timestamps_inside_scripture_quotes` считает глубину кавычек по всей строке
  секции; при несбалансированных « » — не трогает ничего (safety). Покрыто 5/5
  вхождений в дампах.
- **Артефакт `.*.`**: строка Писания в ITALIC, кончавшаяся закрывающим одиночным
  `*`, получала QA-точку СНАРУЖИ курсива. Новая ветка `elif s.endswith('*')` в
  `_ensure_trailing_period` кладёт точку ВНУТРЬ. Покрыто 4/4 вхождения.
- **Дубликат имени автора в заголовке** («…Джон МакАртур — Джон МакАртур»):
  видео-тайтл уже кончается именем проповедника. `join_title_author`
  (core/text_utils.py) не дублирует хвост; применён в заголовках 3 страниц
  (telegraph_pages) и в блоке «Читать также» (md_telegraph). Покрыто 5/5.
- **Штамп «Поверхностное чтение упускает…»**: повторялся дословно из проповеди в
  проповедь — его якорил few-shot-пример + инструкции-заголовки STUDY-промпта
  (классический anchoring-баг, как R13/R40). Формулировки разнесены, смысл
  «копай глубже беглого чтения» сохранён; никаких запретов на сам приём. Клише
  «семантический узел активирован при…» добавлено в существующий запрет сухих
  узловых штампов.
- **Полноширинные CJK-скобки 【…】** из ютуб-тайтла в русском заголовке → круглые
  (normalize_title_text).
- **Ис. 53:5 «мучем за беззакония» → «мучим за беззакония»** (Синодальный): в
  Разборе/Размышлении промах, в Конспекте верно. Якорь по «мучем за» — без
  контекст-слепой замены (урок R40).

Сознательно НЕ трогали (защита ради защиты): расширение guard'а про over-reach в
Размышлении (преувеличение→исповедание) на TYPE 1/3 — R45-правка работает, дампы
чистые; LRM (U+200E) после иврита в «(חָפֵץ‎, евр.)» — он там нужен для
правильного направления запятой, снятие было бы багом; orphan `**` на таймкоде —
уже снимает `_final_telegraph_polish` на публикации (проверено end-to-end).

Тесты: tests/test_v3_r49_synopsis_quality.py. verify_repo — зелёный,
регрессий нет (легитимные таймкоды по-прежнему линкуются, проверено end-to-end на
реальном контенте дампов).

### Контент-аудит прозы (отдельный проход, свежие глаза)

Дополнительно — адверсариальный аудит именно русской ПРОЗЫ (богословская точность,
галлюцинации, over-reach, повторы). Все 4 `Разбор материала` — богословски точны
(сверены цитаты LBCF 8/10/11/15, Вестминстер 11, Халкидон, Орандж 529, Дорт 3/4,
глоссы иврита/греческого — верны). Найденное:

- **Код-фикс (Синодальная точность)**: Пс. 2:10 «уразумейте, цари» → «вразумитесь,
  цари» — добавлен якорный typo (как «мучем→мучим»). Это единственная находка
  контент-аудита, которую корректно чинить детерминированно.
- **НЕ код-баги, а ошибки модели в УЖЕ опубликованном контенте** (детерминированным
  скраббером не ловятся, код задним числом их не исправит — нужна регенерация
  конкретных страниц): (1) галлюцинация «миссионеры в Бразилии, на острове Минданао»
  (Минданао — Филиппины; в свидетельстве Вошера острова нет) — Trus-i-Lzhec L45;
  (2) кривой перевод «никогда не сэкономлю на сравнении» (англ. «measure up») —
  Trus-i-Lzhec L73; (3) телескопирование Страстной недели (обличение в Храме →
  сразу «Распни Его!») — Razmyshlenie-Porazitelnyj L49; (4) register-clash «скрытый
  криминал сердца» — Razmyshlenie-Trus L57; (5) мягкий over-reach про «ярлыки травмы».
- **Эмерджентный повтор через проповеди** (НЕ якорится промптом — grep пуст, значит
  не few-shot): «уклоняясь от личного покаяния» в двух Размышлениях. Чинить нечем,
  кроме запрета (оператор их не хочет); к тому же обе проповеди про национальное
  покаяние Израиля, так что применение уместно в обеих. Оставлено.
- **Устаревшая, но валидная ссылка**: «Читать также» ведёт на слаг `…-05-27`
  (более ранняя публикация той же проповеди). Страница на Telegraph существует —
  ссылка не битая; «устаревшесть» — следствие перепубликации, не баг рендера.
