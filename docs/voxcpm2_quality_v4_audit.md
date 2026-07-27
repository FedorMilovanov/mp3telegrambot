# VoxCPM2 Dub Studio — Quality v4 audit

## Scope

This audit compares the supplied original `The Strength of a Godly Woman | John Piper Clip.mp4`
with the bot-produced Russian dub.  It also compares the generic bot runtime with the previously
successful no-bot V3.2 NoChew workflow recorded in the project research diary.

The translation itself was not treated as the failure.  The investigation targets delivery:
noise, start artifacts, unstable local delay, missing source coverage and mastering.

## Measurements from the supplied files

| Metric | Original | Bot output |
|---|---:|---:|
| Duration | 59.118 s | 59.059 s |
| Audio | AAC stereo, 44.1 kHz | AAC stereo, 48 kHz |
| Integrated loudness | about -19.0 LUFS | about -13.9 LUFS |
| Loudness range | about 3.2 LU | about 6.6 LU |
| True peak | about -6.2 dBFS | about -4.1 dBFS |

The source bed embedded in the final dub has one stable alignment offset of about 9.8 ms.
There is no cumulative clock drift.  The perceived early/late instability is local to the
Russian synthesis blocks.

Detected Russian speech blocks:

1. 3.728–15.768 s
2. 16.668–24.338 s
3. 26.958–37.168 s
4. 40.018–48.528 s
5. 49.448–58.068 s

The corresponding scheduled low-level segment starts are approximately 3.35, 16.25, 26.40,
39.55 and 48.85 s.  Therefore the generated candidates add roughly 0.32–0.55 seconds of
variable leading silence on top of the configured 420 ms Russian delay.

The original contains speech from the opening, while the Russian voice begins around 3.73 s.
The first several seconds were not merely delayed: the selected subtitle/transcript coverage did
not provide a complete opening anchor.

The source component in the mastered output measures closer to roughly 0.24 amplitude than the
requested 0.18.  The cause is the second whole-mix loudness normalization, which raises source,
synthetic voice and both noise floors after they have already been mixed.

## Differences from the successful no-bot V3.2 NoChew workflow

The no-bot workflow established these invariants:

- natural `min_len=2`;
- `retry_badcase=False` in the model call;
- external deterministic candidate selection;
- retry only a segment with a suspicious tail or failed quality gate;
- never slow a short utterance to fill the whole timing window;
- use the B/extended reference for ordinary speech and C/composite for the ending;
- trim a suspicious resumed tail only inside an already detected silence.

The generic bot later added a second conditioning layer around that renderer:

- it converted automatically concatenated English reference audio into prompt audio plus an
  ASR-generated prompt transcript;
- it forced effective CFG to at least 1.9 even when the recipe requested 1.8;
- it enabled the model's internal `retry_badcase` while the outer NoChew loop was also generating
  and selecting deterministic candidates;
- it applied FFT denoise and truncation to the concatenated references;
- it accepted 7.5–13.5 second blocks with only their outer boundaries anchored;
- it checked word identity through Whisper, but did not reject variable leading silence,
  start chirps, high-frequency noise or poor prosodic alignment.

These changes explain why the bot could regress even though it still called a file named
`NoChew`: the actual model conditioning and post-processing were no longer equivalent to the
successful no-bot process.

## External research review

Forty targeted searches were performed across the official OpenBMB VoxCPM/VoxCPM2 repositories,
model cards, technical report, changelog, API documentation, GitHub issues and recent dubbing
alignment research.  The load-bearing conclusions were:

- VoxCPM2 output can contain variable leading/trailing silence and prompt/reference leakage;
- users have reported start chirps/clicks and long-form speed/timing drift;
- higher CFG increases conditioning pressure but can also increase artifacts/noise;
- short sentence-level generation is more stable than long paragraphs;
- clean reference edges and explicit output edge trimming are standard practical safeguards;
- utterance-level duration alone is insufficient for dubbing: local phrase and pause alignment
  are needed;
- `denoise` is reference preprocessing, not a repair for generated noise, and is unnecessary or
  harmful when a clean reference is already available.

Primary sources reviewed include the official VoxCPM2 README/model card and generation API,
the official technical report, official changelog, OpenBMB issues #272 and #302, and recent
research on prosodic/isochronous dubbing alignment.

## Quality v4 production contract

### Transcript and timing

- Creator captions remain preferred.
- Caption coverage is checked against actual source-audio onset.
- A missing opening is recovered with Whisper coverage rather than silently dropped.
- Gemini mode uses local semantic blocks with a target around 4.8 seconds and a hard maximum
  around 7 seconds.
- Ready-SRT mode treats user cues as local timing anchors and does not collapse the entire
  translation into large 8–12 second islands.

### Voice reference

- Prefer coherent speech runs over arbitrary cue fragments.
- Trim unstable edges from every extracted part.
- Join multiple necessary parts with short equal-power crossfades.
- Use gentle peak/RMS normalization only.
- Do not apply FFT denoise or whole-reference loudness pumping.

### Synthesis

- Restore reference-only NoChew.
- Preserve recipe CFG instead of forcing 1.9.
- Keep `retry_badcase=False` inside VoxCPM2.
- Keep the external deterministic candidate loop and semantic Whisper gate.
- Penalize candidates with excessive leading silence or an isolated start chirp.
- Trim every selected candidate to a fixed 65 ms preroll and 140 ms postroll before timing fit.
- Continue to reject segments requiring more than 1.50x acceleration.

### Mastering

- Loudness-normalize the Russian voice once.
- Mix the source after Russian mastering at the requested constant amplitude.
- Do not loudness-normalize the completed mixed bus.
- Use a limiter with auto-level disabled, so it can only reduce exceptional peaks and cannot
  raise the source/noise floor.

## Acceptance test for the next render

For the same Piper clip:

1. no missing opening thought;
2. configured delay remains 420 ms, with candidate onset variation under about 100 ms;
3. no isolated start chirp/click at any segment boundary;
4. no internal phrase drift caused by a 10–13 second timing island;
5. original source bed remains close to the requested 18% before any peak-only attenuation;
6. every Russian segment passes acoustic and Whisper semantic QA;
7. the synthesis report records Quality v4 edge trims and selected candidates.

A physical VoxCPM2 A/B rerender remains a local Windows test because the model archive and CPU
environment are stored on the user's machine.
