# Clean Expressive Dub Studio

## Core rule

Short synthesis windows are retained for timing stability, but they are not
allowed to become isolated emotional islands.  The production path derives one
smoothed performance arc from the source recording and applies that arc through
real source voice references.

The bot and manual PowerShell execution still call the same original files:

- `voxcpm2_cpu_shorts_production.py`
- `master_constant_mix.py`

There is no TTS subprocess proxy, `runpy` renderer, prompt-transcript rescue or
nested semantic wrapper in the production route.

## What preserves a living delivery

### 1. Rhetoric-preserving translation

Gemini MAX receives the whole clip as discourse context and performs three
separate editorial passes:

1. accurate literary-spoken translation;
2. bilingual fidelity and rhetoric review;
3. recording-direction pass for cadence, breathing and continuity.

Purposeful repetition, direct address, rhetorical questions, contrast and
climax must remain.  Duration compression is applied only to overloaded IDs and
is explicitly forbidden from deleting these rhetorical devices.

### 2. Short audio windows, long performance arc

Each source-aligned segment is measured for:

- median and upper F0;
- voiced ratio;
- RMS energy;
- active-speech ratio;
- internal gaps;
- source speaking rate.

The resulting expression score is smoothed across neighbouring segments and an
adjacent jump is capped.  This preserves builds and releases while avoiding the
robotic pattern `calm -> shout -> calm` caused by noisy local measurements.

### 3. Timbre and expression are separated

`extended_reference.wav` is the stable calm timbre anchor.

`composite_reference.wav` is overwritten, when safe material exists, with a
controlled-expressive source reference.  Candidate windows must contain stable
speech and are rejected for excessive F0, excessive loudness, long internal
gaps or near-shouting delivery.

Only `emphatic` and `passionate` arc sections use the composite profile.  The
other sections use the calm profile.  If no safe expressive material exists,
the system keeps the calm fallback rather than manufacturing emotion.

`style_instruction` in `segments_ru_final.json` and
`expressive_continuity.json` is an audit description.  It is **not** injected as
text into VoxCPM and is not a hidden prompt.  The actual control mechanism is
`reference_profile` selecting a real WAV from the speaker.

### 4. Independent release QA

After direct rendering, the existing independent gate checks:

- Russian ASR fidelity;
- clipping and acoustic validity;
- onset and trailing silence;
- internal discontinuities;
- voice F0 against the selected reference profile.

At most one direct retry of failed IDs is permitted.  The QA layer never
replaces the renderer or invents a new synthesis mode.

## Transparent files

Each clean expressive render can produce:

- `output/expressive_continuity.json`
- `references/extended_reference.selection.json`
- `references/composite_reference.selection.json`
- `audio/<video_id>_ru_timeline.clean_qa.json`
- `output/clean_production_report.json`

The expression report records every segment's source metrics, smoothed score,
tier, performance note and actual reference profile.

## Human release check

Automated emotion recognition is not used as a hard release gate.  The final
Russian-only video must be listened to as a continuous paragraph or complete
clip, not only as isolated segments.  The reviewer checks whether emphasis
builds across clauses, whether questions sound genuine, whether repeated words
carry deliberate stress, and whether strong passages remain controlled rather
than shouted.
