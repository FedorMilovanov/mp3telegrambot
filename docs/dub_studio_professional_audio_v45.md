# Dub Studio Professional Audio v4.5

Professional Audio v4.5 is the release gate above the proven NoChew Quality v4.2 core.
It was introduced after a real John Piper clip exposed three independent defects:
legacy long speech islands, per-segment consumption of the global Russian delay, and
voice references selected by duration rather than by a calm representative register.

## Production rules

- Gemini and ready-SRT speech islands are capped at 5.4 seconds.
- Full repair of a legacy project migrates long islands without changing one Russian word.
- `russian_delay_ms` shifts the complete Russian timeline; it is not removed from every phrase window.
- Voice references are selected from clean, sustained, relatively calm source windows.
- Reference selection is written beside each WAV as `*.selection.json`.
- Candidate selection penalizes long internal gaps, abrupt cutoffs and a register materially above the reference.
- Final QA checks continuity and pitch for every segment and retries only failed IDs.
- A release fails instead of delivering audio that remains too high, broken or discontinuous after all QA rounds.
- The Russian master targets -16 LUFS / -1.5 dBTP; the source bed remains at the exact requested gain and the full mix is not normalized again.

## Legacy project repair

Run:

```text
/dubfix PROJECT_ID all
```

The repair keeps translation, title and subtitles local and does not call Gemini. Before
migration it writes `segments_ru_final.pre_v45.json`. It then regenerates the Russian SRT,
voice references, all selected audio segments, the Russian timeline and both final videos.

## Diagnostic artifacts

- `segments_ru_final.pre_v45.json` — immutable pre-migration segment backup.
- `references/extended_reference.selection.json` — selected calm windows and measurements.
- `references/composite_reference.selection.json` — composite selection report.
- `audio/*_ru_timeline.semantic_qa.json` — final per-segment semantic/timing report.
- `audio/*_ru_timeline.semantic_qa_v4.round*.json` — QA round details.
- `output/audio_repair_child.log` — complete child-process log.
- `output/audio_repair_report.json` — repair summary.

## Failure is preferable to a bad release

A strict QA failure is intentional. Use the reported segment IDs with `/dubsegments` and
repeat only those IDs after inspection. Do not weaken pitch, continuity or cutoff gates merely
to force a green result.
