# AGENTS.md — mp3telegrambot engineering contract

Scope: the whole repository. This file is for AI/code agents and human maintainers. Follow it before changing production code.

## Mission

This bot is used continuously to generate, inspect, repair, and archive theological conspects and related Telegram outputs. The goal is not to hide bad outputs by default; the goal is to detect defects, log them, repair pages automatically when possible, and improve generators so future conspects are good enough to review and publish.

## Non-negotiable quality policy

1. **Repair/log/history first.** Do not make “do not publish bad conspect” the default behavior. Keep outputs available for review, record quality status, and improve generation/repair.
2. **Blocking publication is opt-in only.** Do not introduce default blocking unless explicitly gated by these flags:
   - `CONTENT_AUDIT_BLOCK_PUBLICATION=1`
   - `PAGE_AUDIT_BLOCK_PUBLICATION=1`
   - `AUDIT_BLOCK_PUBLICATION=1` for global page-audit blocking
3. **Telegraph token means programmatic repair.** If a Telegraph token is configured, repairs should be performed by code/tools, not by a manual-operator workflow.
4. **Gemini quality over fallback noise.** Keep strict root-analysis behavior by default. Do not silently downgrade theological/AI pages to weak fallback content when Gemini root analysis fails.
5. **No secret leakage.** Never commit or print live tokens, `.env`, `.git/credentials`, `.netrc`, database dumps, or logs with secrets.
6. **Every discovered quality issue gets history.** Append concise entries to `docs/quality_audit_history.md` for production-relevant audit/fix decisions.

## Required verification before handoff

Install dev dependencies if needed:

```bash
python -m pip install -r requirements-dev.txt
```

Run the repository verifier:

```bash
python tools/verify_repo.py
```

This must pass before commit/push unless the user explicitly asks for a partial patch. The verifier runs compileall, pytest, ruff, and `git diff --check`.

## Regression-test discipline

- Add or update regression tests for every bug fix, quality guard, parser rule, prompt-health rule, source-card rule, Telegraph repair behavior, or Telegram formatting fix.
- Prefer deterministic tests over live network/API tests.
- If adding regex/postprocess logic, add a focused regression test.
- Keep tests free of real secrets and runtime artifacts.

## Prompt and theological-output rules

- Do not add literal “bad examples” to production prompts if the model may copy them. Use pattern-level wording instead.
- Keep `/prompthealth` clean: known leaky literals in live prompts should remain zero.
- Avoid weak wrappers such as “author shows/explains” patterns in generated editorial language.
- Do not recommend Tim Keller; keep the denylist/source-card drop behavior silent.
- Source cards must be conservative: preserve known official Russian titles, avoid inventing Russian titles, and silently drop disallowed authors.

## Telegraph pages and archive rules

- New Telegraph pages should be audited and auto-repaired after publish when configured.
- Do not ignore chained/multipart Telegraph pages; expand chains and repair all parts when tooling supports it.
- Keep raw markdown artifacts, split-hyphen artifacts, broken navigation, and source-card problems covered by deterministic tests.
- Persist repair/audit metadata in the generated-pages archive when runtime workflow touches published pages.

## Telegram output safety

- Any user/archive-derived text sent with `parse_mode="HTML"` must be escaped before insertion into tags.
- Do not truncate already-escaped HTML or already-built HTML in a way that can cut tags/entities. Use helper functions that trim plain text first, then escape.
- Respect Telegram limits for captions, messages, poll questions/options, and quiz explanations.

## Quiz/test quality

- Quiz polls should be real comprehension tests, not obvious guessing games.
- Require four meaningful options; reject placeholders, all/none patterns, trivial yes/no, and category-mismatched distractors.
- Wrong answers should be plausible near-alternatives that are wrong specifically by the material.
- Keep send-time sanitization as a final guard even when parser validation exists.

## Git hygiene

- Keep runtime DB/log/download artifacts out of git.
- Before committing, check:

```bash
git status --short
git diff --check
```

- Commit focused changes with tests. Push after meaningful commits when credentials are available.

## Title casing rule (operator-confirmed, 2026-07-05)

Russian material titles use **Title Case**: every significant word is
capitalized; prepositions/conjunctions/particles stay lowercase
(«Трус и Лжец», «Вопросы и Ответы о Спасении»). Sentence-case for titles
is a known past regression — do not reintroduce it. Implemented via
`title_case_fragment` → `sentence_case_russian_title(aggressive_title_case=True)`.
Also: if the audio analysis invents a title with ~zero overlap with its own
timestamps (`title_topic_low_overlap`), the invented `real_title` is dropped
and the real YouTube title is used everywhere (caption, Telegraph, search).

## Timestamp & card-visual rules (operator-confirmed, 2026-07-05)

1. Inline ⏱ timestamps ALWAYS stand BEFORE the sentence period, including in
   headings/sub-headers: «…Духа ⏱ 11:29.» — never «…Духа. ⏱ 11:29» and never
   «…Духа. ⏱ 11:29.» (double period). Deterministic fixer
   `_fix_ts_period_order` in md_telegraph runs on the MARKDOWN stage BEFORE
   timestamp linkification (after it the ⏱ is split into an <a> node and
   string rules can't see it) plus again node-side; do not remove either.
2. Cards with a bold header («**Термин** — описание», «**От…к….** Текст»)
   are rendered WITHOUT the leading • bullet (the bold anchors the card).
   Short list items, scripture blocks («• **Мф 7:21:** *«…»*») and source
   cards («• **Название**, Автор») keep the bullet. Enforced BOTH ways
   (R12, 2026-07-06): render-side `_strip_card_bullets` in md_telegraph
   (content path AND blocks path) + prompt templates no longer prescribe
   «• » before bold card headers — do not reintroduce either.
3. Timestamp-coverage repair must preserve the topic style of the original
   list (one **bold key phrase** per topic) — repaired lists must not strip
   caption bold.

## No default-vocabulary injection rule (operator-confirmed, 2026-07-06)

**Root cause discovered in R13:** a prompt that repeats the SAME concrete
worked example (term, phrase, connector word, scripture ref) across many
rule sections trains the model to reproduce that exact example regardless
of whether the actual source material calls for it — few-shot anchoring.
Live evidence: `STUDY_ANALYSIS_PROMPT` used «Спасение господством»
(Lordship Salvation) / «Лёгкое верие» (Easy Believism) as THE calque-format
example 9 times across the file; 7/13 and 9/13 Study Analysis pages from one
playlist run (sermons on prayer, on the fear of God, on the vision of Christ
in Revelation — none of which substantively debate those two doctrines)
contained them anyway, because the model was pattern-matching the prompt's
own illustration instead of extracting terms from the specific sermon.

**Rule for all future prompt edits:**
1. Never introduce a new worked example (theological term, connector word,
   phrase, scripture reference) and then reuse the EXACT SAME example across
   more than 2-3 rule sections in the same prompt. If a format rule needs
   illustrating in multiple places, rotate through different concrete
   examples each time — the format is what's being taught, not the specific
   term.
2. Any illustrative example that names a real doctrine/term must be
   explicitly marked as a format sample, not a content requirement (e.g.
   "это образец формата, не чек-лист обязательных терминов").
3. When a fix targets one specific over-anchored pair (like R13), also grep
   the rest of `core/prompts.py` for OTHER terms/phrases repeated ≥4 times
   as worked examples — the same mechanism silently creates the next
   instance of this bug. `Total Depravity` / `Age of Accountability` were
   checked in R13 (repeated 4-5x each) and found low-risk empirically (0
   occurrences across 13 real dumps) — re-check if they start appearing in
   unrelated material.
4. `STUDY_ANALYSIS_PROMPT` has a hard diet ceiling (<60000 chars, see
   `test_v3_patch9.py`/`test_v3_minor_debt6.py`) — anti-anchoring fixes must
   stay concise (a short "СТОП" block + a self-check line), not verbose
   essays, or they will blow the budget and Gemini 3.x follows the prompt
   worse overall.

## Synopsis fidelity and multipart rule (operator-confirmed, 2026-07-23)

- `SYNOPSIS_PROMPT_V2` is a transcript product, not an analytical summary.
  Preserve the speaker's wording, order, examples, stories, transitions,
  rhetorical force, and scriptural exposition as fully as the source permits.
- Do not “improve”, shorten, summarize, editorialize, or optimize this prompt
  while fixing Study Analysis. A concise paraphrase is a regression.
- Telegraph may split one long transcript into 2, 3, 4, or more linked parts.
  Multipart output is acceptable and preferable to losing verbatim content.
  Never introduce an arbitrary maximum-parts cap for Synopsis.
- Fix only real defects in Synopsis: missing material, invented wording,
  malformed Markdown, broken navigation, incorrect/out-of-order timestamps,
  duplicate/lost parts, and Telegraph rendering defects.

## Study Analysis depth rule (operator-confirmed, 2026-07-23)

- Study Analysis is the research layer, not a second Synopsis and not a list of
  generic definitions. Prefer 2–5 material-specific, deeply distinguished
  concepts over 5–10 interchangeable dictionary cards.
- Every concept must be anchored in the actual sermon/lecture by a precise
  argument, scripture reference, quotation, or timestamp. A block that could be
  pasted unchanged into another sermon is filler and must be omitted.
- Original-language study is verse-first, never Greek/Hebrew for display.
  Require the exact verse, Russian phrase, word form in that verse, lemma,
  readable Russian pronunciation, basic meaning, contextual meaning, role in
  the material, limit of the lexical claim, source, and timestamp. Zero word
  studies is a valid result; an incomplete decorative lexicon card is dropped.
- Keep dictionary meaning, contextual exegesis, the preacher's use, and pastoral
  application as separate logical levels. Never present an application as if it
  were the lexical meaning of one word.

## Study orthodoxy pair-card rule (operator-confirmed, 2026-07-23)

- The Study-only section title is exactly **«Заблуждения и ответ ортодоксии»**.
  Do not rename it to a neutral editorial heading.
- Use the section only when the source materially raises a concrete error,
  heresy, or doctrinal substitution; do not manufacture controversy.
- Every item is an inseparable pair of two separate paragraphs:
  1. `**Название проблемы** ❌ **Подмена: название заблуждения.** ...`
  2. `✅ **Ответ ортодоксальной церкви.** ...`
- Preserve both markers, the fixed answer label, paragraph separation, concrete
  Scripture/confession/council support, and timestamp when present.
- This ❌/✅ pair format belongs to Study Analysis only. Reflection must not copy
  it, and Reflection cleanup must never remove it from Study Analysis.

## Teacherly Study prose rule (operator-confirmed, 2026-07-23)

- The public Study page is a coherent teaching chapter, not the visible answer to
  an internal checklist. Hidden schema fields may protect accuracy, but labels
  such as “Basic meaning”, “In this verse”, “Role in the argument”, “Limit of the
  claim”, and “Source” must be woven into natural Russian prose, never printed as
  a questionnaire or field list.
- Let the material choose its architecture. Use material-specific headings and
  connected paragraphs; do not force the old section taxonomy or a fixed number
  of definition cards. Lists are exceptional and only justified by a real finite
  distinction or sequence in the source.
- Definitions and original-language observations belong inside the argument.
  They must advance the reader from thesis to basis, distinction, and consequence.
  Greek/Hebrew must not be decorative, must not be explained by root-splitting
  alone, and one word must never be made to prove a whole doctrine.
- Use semantic **bold anchors** inside paragraphs for real theses, contrasts, and
  turning points. Do not bold every noun and do not publish long unaccented walls.
- Begin directly with the truth or problem. Ban meta-introductions such as “this
  material discusses” and ban descriptions of the generation process.
- A beautiful synthesis is allowed only when the transcript supports it. For
  heterogeneous Q&A, cluster related answers without inventing a false single
  system. Attribute disputed confessional claims to the speaker or tradition.
- `services.study_synthesis_runtime` is the final effective Study prompt layer.
  Do not remove it by restoring the legacy rubric prompt unless the operator
  explicitly reverses this decision.
