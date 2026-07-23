# Quality audit — «Анатомия Церкви» live run (2026-07-23)

Source: operator-provided production log and screenshots for three John MacArthur
materials:

- `4wmCcHMcP90` — «Анатомия Церкви (Введение) Скелет»;
- `Q7GaQgMU5Ek` — «Анатомия Церкви (Часть 1) Внутренние Системы»;
- `zNlGBuvz2qA` — «Анатомия Церкви (Часть 2) Внутреннее Устройство».

## Operator-confirmed product boundaries

1. **Synopsis is a transcript product.** It must remain maximally verbatim and
   preserve the author's order, wording, examples, stories, exposition, and tone.
   A four-part Telegraph chain is acceptable; splitting is not a defect when it
   protects full content.
2. **Study Analysis is the research product.** It must not become a second
   Synopsis or a generic dictionary/list generated from broad topic words.
3. **Study TYPE 6 is visually fixed.** The section title remains
   «Заблуждения и ответ ортодоксии». Every item remains a two-paragraph pair:
   `❌ Подмена` followed by `✅ Ответ ортодоксальной церкви.`
4. **Original-language analysis must be verse-first.** Greek/Hebrew is included
   only when it adds exact contextual value for a named biblical verse.

## Findings

### P0 — thin lexicon cards survived retry and publication

The Study content audit detected missing `role_in_argument` and thin dictionary
cards (`shanan`, `hypomnēsis`, `didaskalia`). The retry reported `6 -> 6`, so it
fixed none of the six warnings, but the page still published. The old expanded
schema required only `lemma` and `role_in_argument`, while the renderer reduced
that to `**lemma** — role`, encouraging decorative vocabulary.

### P0 — original-language section lacked reader context

The visible output listed transliterated lemmas without reliably stating:

- the biblical book/chapter/verse;
- the Russian phrase being explained;
- the exact inflected form in the verse versus the dictionary lemma;
- readable Russian pronunciation;
- the lexical source;
- the boundary between lexical meaning, contextual exegesis, the preacher's
  argument, and pastoral application.

This makes the section look academic without helping a Russian-only reader.

### P0 — Study definitions were quantity-driven

TYPE 1 requested 5–10 definition cards. Even though the prompt asked for depth,
the quantity target and repeated card template encouraged generic definitions
of broad words such as obedience, humility, love, and unity. A definition that
could be pasted into an unrelated sermon is not material-grounded analysis.

### P1 — published typo remained unresolved after repair

The page audit found `Слово Божьего — нструмент...`. The repair path reported a
change but the following audit still marked the typo unresolved. The fix now
adds phrase-anchored normalization rather than a dangerous global grammatical
replacement.

### P1 — one inline timestamp preceded its section start

A section started at `44:06` but contained inline `44:00`. The fix now reconciles
only near-boundary differences up to 30 seconds and preserves genuine references
to much earlier moments.

### P1 — large Gemini tasks overlapped and exhausted quota

The three long videos launched root analysis, Synopsis, Study, Reflection,
Shorts, Clips, and extras close together. The run hit both input-token-per-minute
and requests-per-day limits. Encoding/download work may remain parallel, but
large Gemini generations need a quality-first queue.

### Not defects

- Missing VK/RuTube matches are expected: the operator has not uploaded these
  fresh videos to those platforms yet. The matcher correctly rejected a weak
  RuTube candidate below threshold.
- Multipart Synopsis is expected and must not be capped.
- Telegram captions may show fewer timestamp lines because of caption limits;
  all generated chapter markers remain stored in MP3/archive metadata.

## Fix in this patch

- Recorded immutable operator rules in `AGENTS.md`.
- Added a late, idempotent Study quality contract without modifying either
  Synopsis prompt.
- Reduced definition pressure to 2–5 material-specific concepts.
- Replaced the old public TYPE 3 heading with
  «Ключевые слова в контексте Писания» and set a valid range of 0–3 blocks.
- Added a full `word_study` structured schema.
- Deterministically drops incomplete legacy lexicon cards while preserving the
  rest of the Study page; the old `normalize(...) or raw` fallback can no longer
  resurrect a rejected thin card.
- Preserved and explicitly protected the Study-only ❌/✅ orthodoxy pair format.
- Added anchored repair for the observed `Слово Божьего — нструмент` defect.
- Reconciles a section start with an inline timestamp only when the difference
  is 1–30 seconds; large backward references remain untouched.
- Added focused regression tests.

## Remaining audit queue

1. Add a per-project semaphore/queue for large Gemini calls while keeping
   non-AI media work parallel.
2. Run the saved generated-page archives through the DOM/Playwright auditor and
   compare every Synopsis, Study, Reflection, Terms, and navigation page.
3. Re-run the already-published affected Telegraph page through the runtime
   repair tool so the historical typo is corrected remotely, not only prevented
   in future generations.
