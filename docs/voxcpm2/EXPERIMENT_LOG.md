# VoxCPM2 experiment log

This is the append-only laboratory log for the local CPU dubbing work. Keep observed facts separate from hypotheses. Do not rewrite failed runs as successes.

## Environment baseline

```text
Date:          2026-07-25
OS:            Windows
Python:        3.11.9
voxcpm:        2.0.3
PyTorch:       2.13.0+cpu
CUDA visible:  False
CPU threads:   10
Draft steps:   4
CPU venv:      C:\AI-Archive\VoxCPM2-CPU-TEST\.venv
Model archive: C:\AI-Archive\VoxCPM2-paused-RTX3060
```

## Hardware incident record

The NVIDIA GeForce RTX 3060 is treated as confirmed hardware-defective for this workload.

Observed symptoms include `nvlddmkm` Event ID 153, LiveKernelEvent/WATCHDOG, temporary Windows freezes, CUBLAS internal/execution errors, illegal memory access, and BF16/FP16 failures. Driver 610.62 did not fix the problem and reballing was already performed.

Project rule: never use VoxCPM2 CUDA on this machine before the GPU is replaced. Every run must show `CUDA available: False`.

## Runs

### R01 — first CPU voice-design smoke test

```text
Status:               success
Generated duration:   10.72 sec
Synthesis time:       101.37 sec
RTF:                  9.46
Estimated 42:23 run:  6.68 hours
Model load:           about 17 sec
```

Listening notes:

- Russian pronunciation was good.
- The voice itself was interesting.
- The audio had a noisy, reverberant, "garage" character.

Decision: CPU inference is viable, but reference selection and acoustic preparation matter more than aggressive post-EQ.

### R02 — MacArthur one-pass combined clone

Source:

```text
https://youtube.com/shorts/RAaSAbPj-iw
```

Failure:

```text
The expanded size of the tensor (512) must match the existing size (550)
Target sizes: [1, 2, 512, 128]
Tensor sizes: [2, 550, 128]
```

Diagnosis: prompt/reference prefill exceeded the snapshot's effective KV-cache length.

### R03 — MacArthur prompt-only retry

Failure:

```text
KV cache is full
```

Diagnosis: removing `reference_wav_path` did not address the actual cache-length limit.

### R04 — MacArthur with explicit 8192-position KV cache

The model loaded successfully and logged:

```text
KV cache expanded to 8192 positions.
```

One-pass generation technically completed:

```text
Generated WAV:  19.20 sec
Synthesis:       213.80 sec
RTF:             11.14
Source video:    48.69 sec
Required atempo: 0.394299
```

The launcher correctly rejected the result because stretching 19.20 seconds to 48.69 seconds would produce unnaturally slow and likely incomplete speech.

Diagnosis: premature stop / incomplete long single-pass generation, not an FFmpeg failure.

### R05 — segmented MacArthur V2

```text
Status:              technical success, quality revision required
Final MP4 duration:  48.615 sec
Final audio:         48 kHz stereo AAC
Segments:            7
Clone mode:          reference-only
```

Architecture:

- seven source-aligned Russian segments;
- model loaded once;
- same reference re-injected for every segment;
- per-segment `min_len` and `max_len` derived from target window duration;
- raw and fitted WAVs stored separately;
- exact timeline assembled with `adelay` and `amix`;
- final sidechain mix keeps quiet English under Russian speech;
- clone mode defaults to `reference` for English-to-Russian transfer.

Output root:

```text
C:\AI-Archive\MacArthur-Short-RAaSAbPj-iw-V2
```

Owner listening review:

- the complete Short now exists;
- several phrase endings sound swallowed or cut;
- independent segment transitions make the speech less fluid;
- room/echo coloration remains audible;
- the result is useful as proof of concept but is not publication quality.

Signal/timeline review of the uploaded final MP4:

- the final media duration is correct;
- hard prosodic resets occur at the independently generated boundaries around 5.12, 10.88, 16.96 and 39.68 seconds;
- source-aligned gaps remain around 24.16-24.72 and 32.60-33.20 seconds;
- the last generated phrase reaches the 48.00-second edge and is the clearest candidate for end trimming;
- V2 applies a 25 ms end fade plus exact `atrim` to every fitted segment, which can attenuate final Russian consonants;
- the English bed is mixed at 0.18, so perceived echo may be a combination of cloned room coloration and the original same-speaker voice underneath.

R05 decision:

1. remove speech-touching end fades;
2. reserve approximately 180-250 ms of protected tail silence per segment;
3. fit active speech into the window minus the protected tail rather than trimming at the spoken edge;
4. merge adjacent segments into fewer 10-15 second thought groups to reduce prosodic resets;
5. reduce the English bed for the next diagnostic mix;
6. compare the clean Russian timeline against the mixed MP4 before attributing all echo to the model;
7. test a cleaner external MacArthur reference after the timing defects are fixed.

## Error catalogue

| Error | Actual meaning | Correct response |
|---|---|---|
| `UnicodeEncodeError: charmap` | Windows stream encoding | Reconfigure stdout/stderr and set UTF-8 environment variables |
| unexpected keyword `seed` | installed API differs from current main | Inspect installed signature; do not assume latest API |
| tensor `512` vs `550` | prompt prefill larger than KV cache | Reinitialize both model caches with a larger length |
| `KV cache is full` | autoregressive sequence reached cache limit | Larger cache for short jobs; segment long text |
| output far shorter than source | premature stop or incomplete generation | Reject global stretch; segment and regenerate |
| garage/room sound | reference/acoustic transfer problem | choose a cleaner reference; use restrained filtering |
| phrase-ending consonants weakened | fade/trim touches speech edge | reserve tail silence and never fade the spoken edge |
| choppy segment joins | independent prosody restarts too frequently | use fewer thought-sized segments and protected transitions |

## Acceptance checklist for every future run

- `CUDA available: False` is present in the log.
- Source URL, model snapshot, clone mode, steps, CFG, thread count and cache length are recorded.
- Every expected segment has a raw WAV, fitted WAV and report record.
- No segment is hidden behind an extreme tempo correction.
- Final audio duration matches the source timeline.
- Russian ASR contains the complete intended translation.
- Scripture references and theological terms are verified.
- First and last words are not clipped.
- English background level is reviewed by ear.
- Failures are appended here with exact traceback and resolution.
