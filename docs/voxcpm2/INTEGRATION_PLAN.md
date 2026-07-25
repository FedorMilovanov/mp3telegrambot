# VoxCPM2 integration plan for mp3telegrambot

This document defines how the laboratory CPU dubbing path should enter the bot without destabilizing the current LiveDub pipeline.

## Non-goals for the first integration

The first production slice will not:

- import PyTorch during normal bot startup;
- use CUDA on the current machine;
- generate an entire sermon in one `model.generate()` call;
- replace Yandex LiveDub before the CPU path passes quality gates;
- hide failed or incomplete generation behind time stretching;
- commit model weights, source video or reference WAVs to Git.

## Target architecture

```text
Telegram request / local command
        |
        v
source acquisition + captions
        |
        v
translation and theological QA
        |
        v
segment plan (text + source windows)
        |
        v
reference selection / preparation
        |
        v
single-job CPU VoxCPM2 worker
        |
        +--> raw segment WAVs
        +--> fitted segment WAVs
        +--> incremental manifest
        |
        v
exact timeline WAV
        |
        v
ASR completeness check
        |
        v
existing LiveDub loudness + sidechain mix
        |
        v
MP4 / MP3 / SRT / JSON report
```

## Proposed modules

```text
services/voxcpm2_runtime.py
    Lazy process owner. Starts no heavy imports until a job is accepted.
    Enforces one active job, CPU-only environment and cancellation.

services/voxcpm2_models.py
    Resolves local snapshot, validates files and records model fingerprint.

services/voxcpm2_reference.py
    Extracts candidate WAVs, applies conservative preparation and stores
    reference metadata without committing copyrighted audio.

services/voxcpm2_segmenter.py
    Converts timed translation into non-overlapping synthesis windows.
    Calculates text density and flags impossible windows before inference.

services/voxcpm2_synth.py
    Loads VoxCPM2 once and generates one segment at a time with explicit
    cache length, clone mode, steps, CFG, min_len and max_len.

services/voxcpm2_timeline.py
    Fits moderate tempo differences, pads/trims exact windows and builds
    a complete timeline WAV.

services/voxcpm2_qa.py
    Runs technical checks, faster-whisper completeness checks and hooks
    into theological/semantic QA.

pipelines/livedub_voxcpm2.py
    Coordinates the modules and returns the same publication artifacts as
    the existing LiveDub path.
```

## Reuse existing project code

Prefer adapters over duplicate implementations.

- `services/livedub_mix.py`: loudness measurement, sidechain policy, tail handling and final media mix.
- `services/livedub_qa.py`: audio-grounded semantic QA and technical checks.
- long-form QA services: sermon-scale review and focused correction.
- existing yt-dlp/FFmpeg helpers: source acquisition and media probing.
- current cancellation/progress mechanism: user-visible progress and `/stop`.
- existing output/publication policy: final Telegram video, MP3 and captions.

## Job manifest

Every job should persist a manifest outside Git so it can resume after interruption.

```json
{
  "schema_version": 1,
  "job_id": "youtube-id-and-timestamp",
  "source_url": "https://...",
  "source_duration": 48.69,
  "model_snapshot": ".../snapshots/<hash>",
  "runtime": {
    "python": "3.11.9",
    "voxcpm": "2.0.3",
    "torch": "2.13.0+cpu",
    "cuda_available": false,
    "threads": 10
  },
  "synthesis": {
    "clone_mode": "reference",
    "steps": 4,
    "cfg": 2.0,
    "cache_length": 2048
  },
  "reference": {
    "source_start": 0.0,
    "source_end": 10.88,
    "processing": "highpass/lowpass/loudnorm",
    "transcript_sha256": "..."
  },
  "segments": [
    {
      "id": 1,
      "start": 0.0,
      "end": 5.12,
      "text": "...",
      "status": "complete",
      "raw_wav": "...",
      "fitted_wav": "...",
      "raw_duration": 5.4,
      "tempo": 1.054,
      "attempts": 1
    }
  ]
}
```

Write the manifest after every segment, not only at the end.

## State machine

```text
CREATED
  -> SOURCE_READY
  -> TRANSLATION_READY
  -> REFERENCE_READY
  -> MODEL_LOADED
  -> SYNTHESIZING
  -> TIMELINE_READY
  -> QA_READY
  -> MIXED
  -> PUBLISHED
```

Failure states retain work products:

```text
FAILED_SOURCE
FAILED_REFERENCE
FAILED_SEGMENT_<id>
FAILED_TIMELINE
FAILED_QA
FAILED_MIX
CANCELLED
```

A retry should continue from the first incomplete state.

## Segment policy

Initial production limits:

- target window generally 3-12 seconds;
- one complete thought per segment;
- no overlap;
- preserve deliberate source pauses as gaps in the timeline;
- reject text density that would require clearly unnatural speech;
- reference is re-injected for every segment;
- one failed segment does not invalidate completed segments.

Tempo policy:

```text
preferred: 0.80-1.25
review:    0.65-1.65
reject:    outside 0.65-1.65
```

The limits refer to `raw_duration / target_duration`, which is the FFmpeg `atempo` factor used by the current laboratory tool.

## Retry policy

A segment may be retried with controlled changes in this order:

1. new seed only when supported by the installed API;
2. small `min_len`/`max_len` adjustment;
3. punctuation or segment-boundary correction without changing meaning;
4. split the segment into two windows;
5. alternate approved reference;
6. alternate clone mode as an explicit A/B run.

Do not silently change multiple variables in one retry.

## Resource policy

- CPU-only process with `CUDA_VISIBLE_DEVICES=-1` set before importing torch.
- One active synthesis job per machine.
- Model loaded once per job or long-lived worker, not once per segment.
- Work directory outside repository.
- Memory pressure and available disk logged before the job starts.
- No CapCut or other heavy workload should be started automatically alongside synthesis.

## QA gates

A job cannot be published until:

1. every expected segment exists;
2. final duration matches source within the project tolerance;
3. no rejected tempo factor is present;
4. Russian ASR covers the intended text;
5. Scripture references and theological terms pass review;
6. no first/last syllable is clipped;
7. no large room/garage artifact makes the voice unacceptable;
8. English bed is audible but does not compete with Russian speech;
9. JSON manifest and logs are saved.

## Delivery milestones

### M0 — laboratory Short

- MacArthur 48.69-second V2 completes.
- Manual listening review.
- Record segment timings, RTF and defects in `EXPERIMENT_LOG.md`.

### M1 — resumable command-line worker

- incremental manifest;
- skip completed segments;
- regenerate selected segment IDs;
- output validation command;
- unit tests for segment parsing and tempo decomposition.

### M2 — bot-private experimental mode

- hidden/admin-only mode;
- one Short at a time;
- progress per segment;
- cancellation;
- no change to default Yandex behavior.

### M3 — quality comparison

- reference-only vs Ultimate A/B;
- steps 4 vs 10;
- reference candidate comparison;
- objective ASR completeness plus human listening notes.

### M4 — long sermon pipeline

- paragraph/sentence segmentation;
- checkpointed overnight execution;
- focused regeneration;
- final semantic QA and Scripture audit;
- publication only after complete report.

## Definition of production-ready

The VoxCPM2 path is production-ready only when it can be interrupted, resumed and audited without redoing completed work, and when it never claims success for a partial voice track.
