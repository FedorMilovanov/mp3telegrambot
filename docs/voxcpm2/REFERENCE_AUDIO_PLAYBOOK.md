# VoxCPM2 reference-audio playbook

Reference quality is a first-order model input, not a cosmetic post-processing detail. VoxCPM2 can transfer room tone, compression, breath pattern, mic distance, accent and prosody together with speaker identity.

## Default policy for this project

For English speaker -> Russian target speech:

```text
Primary mode: reference-only
A/B mode:     ultimate after a successful reference-only run
```

Reference-only:

```python
wav = model.generate(
    text=target_text,
    reference_wav_path=reference_wav,
)
```

Ultimate/combined:

```python
wav = model.generate(
    text=target_text,
    reference_wav_path=reference_wav,
    prompt_wav_path=reference_wav,
    prompt_text=exact_reference_transcript,
)
```

Reference-only is the cross-language default because continuation conditioning may preserve source-language rhythm and articulation more strongly. Ultimate is an A/B candidate when similarity is insufficient.

## Gold reference criteria

A candidate is preferred when all of the following are true:

- one speaker only;
- 5-12 seconds for the first experiment;
- one or two complete phrases, not a clipped fragment;
- stable vocal energy and mic distance;
- no music, applause, audience speech or sound effects;
- no clipping or obvious codec pumping;
- little room reverberation;
- no long silence at either edge;
- no cough, lip smack or breath burst dominating the sample;
- transcript is exact when continuation/ultimate mode is used.

Longer is not automatically better. A long hall recording can provide more bad acoustic conditioning than useful speaker information.

## Scoring rubric

Score every candidate from 0 to 2 in each category.

| Criterion | 0 | 1 | 2 |
|---|---|---|---|
| Speaker isolation | overlap/noise | minor contamination | one clean speaker |
| Room sound | strong echo | audible room | dry/close |
| Background | music/crowd | low steady noise | effectively clean |
| Phrase quality | clipped/disfluent | usable | complete natural phrase |
| Mic consistency | large changes | small changes | stable |
| Transcript confidence | uncertain | mostly exact | word-exact |
| Vocal representativeness | unusual shout/whisper | acceptable | normal target style |

Initial production threshold: at least 11/14, with no zero in speaker isolation, room sound or transcript confidence for Ultimate mode.

## Reference search procedure

1. Extract the highest-quality original audio available; do not convert to MP3 first.
2. Scan several candidate windows rather than accepting the first sentence.
3. Reject windows with applause, music, overlapping voices and hard cuts.
4. Prefer close-mic speech over a dramatic but reverberant passage.
5. Export candidates as mono 16 kHz WAV for the model encoder.
6. Name them deterministically:

```text
speaker_sourceid_start-end_score.wav
```

Example:

```text
macarthur_RAaSAbPj-iw_00.00-10.88_score12.wav
```

7. Keep the unfiltered candidate and processed candidate side by side.
8. Run the same short Russian sentence against the best two or three candidates.
9. Compare identity, pronunciation, room sound and stability before generating a full Short.

## Safe first-pass preparation

Current conservative filter:

```text
highpass=f=65,lowpass=f=7800,loudnorm=I=-20:LRA=7:TP=-2
```

This removes sub-bass rumble, prevents irrelevant ultrasonic content and gives stable input level without pretending to reconstruct a studio recording.

Avoid by default:

```text
afftdn with aggressive thresholds
heavy dereverb
strong multiband compression
large presence boosts
hard noise gates
```

These can create metallic tails, pumping, chopped consonants or an artificial noise floor which the model may copy.

## Denoiser policy

The project currently uses:

```python
load_denoiser=False
denoise=False
```

Reason:

- stable CPU baseline;
- lower memory and dependency complexity;
- reference defects remain observable instead of being hidden by another model;
- the current saved snapshot is reused offline.

A denoiser test should be isolated as an A/B experiment. Do not change the reference, clone mode, steps and denoiser simultaneously.

## Exact transcript policy

When `prompt_wav_path` is present, `prompt_text` must match the audible words, including contractions and repeated words. Do not summarize, translate or silently correct the speaker.

Bad:

```text
The movement made no contribution to doctrine.
```

when the audio actually says three separate sentences.

Good:

```text
The charismatic movement, as such, has made no contribution to biblical clarity. It has made no contribution to biblical interpretation. It has made no contribution to sound doctrine.
```

A transcript mismatch can damage alignment and prompt-cache behavior even when the audio itself is clean.

## Cross-language A/B matrix

Change one variable at a time.

| Run | Mode | Reference | Steps | Purpose |
|---|---|---|---:|---|
| A | reference | best clean candidate | 4 | fastest identity/pronunciation check |
| B | reference | second-best candidate | 4 | reference-quality comparison |
| C | reference | winner from A/B | 10 | final-quality reference baseline |
| D | ultimate | same winner + exact transcript | 10 | similarity vs accent comparison |

Listen for:

- Russian consonant clarity;
- English accent leakage;
- timbre similarity;
- phrase-ending stability;
- room/garage coloration;
- breath and pause naturalness;
- drift between early and late segments.

## Long-form rule

Never use one reference once at the beginning of a 42-minute single generation. Re-inject the same approved reference for every independently generated segment. This re-anchors speaker identity and makes one bad segment replaceable.

## Storage policy

Do not commit copyrighted source WAVs to Git. Commit only:

- source URL and time range;
- exact transcript where permitted;
- extraction/filter command;
- objective measurements;
- listening notes;
- hashes when reproducibility is needed.

Local reference assets belong under the job work directory, outside the repository.
