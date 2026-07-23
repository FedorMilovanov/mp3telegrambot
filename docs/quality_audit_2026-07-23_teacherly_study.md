# Teacherly Study marathon audit — 2026-07-23

## Operator finding

A short free-form request produced a visibly stronger Study text than the accumulated legacy rubric prompt. The better sample had material-specific headings, connected paragraphs, definitions inside an argument, semantic bold anchors, direct entry into the subject, and a final synthesis. The old production prompt instead encouraged section taxonomies, definition quotas, visible field labels, repeated cards, and Greek/Hebrew included to satisfy a form.

The correct conclusion is not “remove all constraints”. It is to separate **public composition freedom** from **hidden reliability constraints**:

- public text should read as a coherent teaching chapter;
- internal audit still guards grounding, confessional attribution, timestamps, original-language accuracy, and Telegraph rendering;
- Synopsis remains a verbatim transcript product and is not touched by this change.

## What the successful experiment teaches

1. **Start with the truth, not metadata.** The reader benefits when the page begins with a doctrinal claim, tension, or distinction. “This material discusses…” wrappers weaken authority and waste space.

2. **Let the material provide the outline.** A fixed sequence such as Concepts → Scripture → Lexicon → Sources makes unlike sermons look identical. Strong headings should name the actual line of thought.

3. **Definitions work best inside reasoning.** A definition becomes useful when it answers a live question and changes the next step of the argument. Detached definition cards tend to become interchangeable filler.

4. **Bold is editorial navigation.** A few bold theses and contrasts make long paragraphs teachable. Bold on every noun becomes noise; no bold in a long section becomes a wall.

5. **Synthesis is valuable but dangerous.** A final unifying line can reveal the architecture of a sermon. In heterogeneous Q&A it can also invent an architecture that no panelist actually stated. Synthesis therefore needs explicit support from transcript, Scripture, or permitted sources.

6. **Length should follow substance.** The “about 1000 words” phrase in the experiment was not a product rule. Study may use the available budget of one saturated Telegraph page. Short material should remain short; deep long material may use roughly 12k–26k visible characters depending on duration and density.

## Hidden defects in the otherwise strong sample

The sample is much better as writing, but it also exposes why completely unconstrained prose is unsafe.

### Decorative or weakly grounded original-language insertions

Several Greek words were introduced because they were thematically related, not because the supplied transcript actually exegeted the exact form in a stated verse. Examples of the risk include adding a generic word for practical wisdom, “door”, “heart”, “doctrine”, or “false teacher” simply because the surrounding topic permits it. These additions can sound learned while teaching nothing the Russian reader could not already understand.

### Etymological fallacy

Breaking a word into roots and turning the parts into the meaning of the whole is not reliable exegesis. A familiar example is presenting repentance as a simple arithmetic combination of “change” plus “mind”. Historical formation can be interesting, but usage in the sentence and corpus controls meaning.

### Lexicon-to-dogmatics slippage

A theological conclusion may be true without being the lexical meaning of one word. “Judicial declaration”, a particular doctrine of inspiration, or a confessional account of divine love must not be smuggled into a dictionary gloss. The page must distinguish lexical observation, contextual exegesis, the speaker’s doctrinal use, and the editor’s synthesis.

### Overstated semantic ranges

Listing every possible association of a word and choosing the most useful one for the argument is unsafe. A broad semantic range does not mean every sense is active in the verse. The prompt now requires the actual contextual contribution, not an impressive list.

### False panel consensus

A Q&A page may contain answers by several speakers. One answer must not silently become the position of the whole panel. The revised prompt requires attribution when speakers differ or when a claim belongs to a tradition.

### Missing time anchors

The free-form sample read well but discarded many available timestamps. Study should not become a timestamp catalogue, yet important claims tied to the source need natural inline anchors.

## Production design adopted

### Effective prompt

`services.study_synthesis_runtime` replaces only the final effective `STUDY_ANALYSIS_PROMPT`. The large legacy prompt remains in source for compatibility, but the model receives a concise material-led brief after all earlier contract installers.

The prompt now requires:

- direct entry into the subject;
- 3–7 material-specific sections as a soft default, not a quota;
- one to three connected paragraphs per section;
- thesis → basis → distinction → consequence as a thinking rhythm, not visible labels;
- lists only for a real finite distinction or sequence;
- semantic bold anchors;
- one-page Telegraph depth budget instead of a fixed word cap;
- no invented quotes, sources, consensus, or stronger claim than the transcript supports;
- original languages only when they change a concrete verse reading;
- preserved Study-only “Заблуждения и ответ ортодоксии” pair format.

### One-page depth profiles

Study ceilings were expanded without changing Reflection:

- short: 3.5k–8k characters;
- balanced/unknown: 8k–16k;
- long (60+ minutes): 12k–22k;
- very long (2+ hours): 16k–26k.

These are upper guides, not minimum quotas. Existing Telegraph `CONTENT_TOO_BIG` recursive publication remains the final safety net.

### Public word-study rendering

Internal lexical fields no longer render as:

- “Russian phrase of the verse:”
- “Basic meaning:”
- “In this verse:”
- “Role in the argument:”
- “Limit of the conclusion:”
- “Source:”

A complete lexical observation is rendered as one coherent paragraph. An incomplete “lemma — generic sentence” card is still dropped.

### New deterministic warnings

Study content audit now detects:

- visible checklist prose;
- four or more fragmented definition cards;
- three or more generic rubric headings;
- long sections without meaningful bold anchors.

These warnings feed the existing Gemini repair pass. A repair that leaves the warning count unchanged is now rejected by the post-import wrapper; the previous `6 -> 6 accepted` behaviour is no longer accepted as improvement.

## Non-regressions

- Synopsis prompts and multipart verbatim output are untouched.
- The fixed “Заблуждения и ответ ортодоксии” layout remains unchanged.
- Reflection does not inherit Study pair cards or Study prose rules.
- Existing Telegraph auto-split and auto-repair remain active.
- No new environment variable is required.
