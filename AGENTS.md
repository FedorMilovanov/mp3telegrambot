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
