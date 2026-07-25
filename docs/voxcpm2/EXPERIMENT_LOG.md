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

### R06 — reference sweep A/B/C/D

Four equal Russian test phrases were generated to compare reference strategy rather than full-video timing.

```text
A: short 10.88-second reference-only
B: extended 24-second reference-only
C: composite ~21-second reference-only
D: short 10.88-second Ultimate/combined
```

Owner review:

- D was clearly the worst result;
- C had the strongest concluding cadence, but the opening was too amorphous;
- B had the best opening and most stable general delivery;
- A was calm and usable, but inserted an unnatural isolated pause around one word.

Decision:

```text
Segments 1-3: B extended reference
Final segment: C composite reference
Production mode: reference-only
Ultimate: excluded from the default English-to-Russian path
```

### R07 — V3.1 hybrid B/C

Architecture:

- four thought-sized blocks instead of seven;
- B reference for blocks 1-3;
- C reference for the final block;
- Steps 10;
- no speech-touching end fade;
- protected tail silence;
- one global compressor after timeline assembly.

Owner review:

- overall voice and prosody were more usable;
- the end of every thought block contained chewing/mumbled artifacts;
- the defect appeared after the phrase had apparently already ended.

Diagnosis:

V3.1 forced `min_len` to approximately 92-95% of each timing window. The sentence could finish naturally, enter silence, then the stop head was prevented from ending and produced a weak speech-like restart. Any subsequent slowdown made the artifact more obvious.

Decision:

- return first attempts to natural `min_len=2`;
- use a modest minimum only for a demonstrably incomplete retry;
- detect silence followed by a short resumed tail;
- never slow a short result merely to fill its source window.

### R08 — V3.2 NoChew

```text
Status:              owner-approved technical baseline
Segments:            4
Clone mode:          reference-only
References:          B for 1-3, C for 4
First min_len:       2
Short slowdown:      forbidden
Tail restart check:  enabled
English mix:         separate from speech validation
```

Owner listening review:

- substantially better than every previous full render;
- no obvious swallowed words;
- phrase endings sounded clear;
- no recurring chewing artifact was noticed;
- the complete Short sounded coherent and usable as the base for a publication render.

This is the first result that must be treated as a stable baseline. Future tuning must not regress its endpoint behavior.

### R09 — final publication candidate

Status at the time of this entry: **running locally; not yet approved**.

Profile:

```text
Steps:             16
CFG:               1.80
Candidates:        two per segment; third only when suspicious
Seed policy:       fixed and recorded
Intermediate:      24-bit / 48 kHz
NoChew:            retained
Short slowdown:    forbidden
Master target:     -14 LUFS / -1 dBTP
```

The initial final launcher required several packaging corrections before synthesis could start:

1. the first distributed PowerShell file contained a real unmatched-expression parse error;
2. manual text replacement caused encoding corruption and cascading parser errors;
3. a replacement launcher was accidentally checked by a relative path resolved under `C:\Windows\System32`;
4. the final work-root directories did not exist;
5. the source video was not copied into the final work root;
6. B/C references were not created before model invocation.

Repository response:

- add an ASCII-safe, argument-array-based self-contained launcher;
- create all directories automatically;
- reuse/download source automatically;
- create references automatically;
- run a lightweight preflight before model loading;
- parse every VoxCPM2 PowerShell file in dedicated Windows CI.

Mix correction discovered while R09 was running:

```text
"reduce original by 25%" = gain 0.75
"leave original at 25%"  = gain 0.25
```

The current expensive Russian synthesis must not be discarded. If its first mixed MP4 is produced with gain 0.25, run only the constant-gain remaster at 0.75 using the same Russian timeline.

Do not mark R09 successful until:

- Russian-only MP4 has been reviewed;
- all four endings and final cadence pass;
- selected attempts/seeds are inspected in JSON;
- a constant-gain 0.75 mix is reviewed;
- master JSON exists;
- final upload file is explicitly approved by the owner.

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
| pause then chewing tail | `min_len` forced generation after natural sentence end | natural `min_len=2`, retry only when truly incomplete, detect resumed tail |
| short speech sounds stretched | `atempo < 1` used to fill a window | preserve natural speed and pad silence |
| PowerShell cascading parse errors | one broken quote/expression or encoding-damaged text | replace launcher; validate every `.ps1` with PowerShell AST in CI |
| parser reports missing file under System32 | relative path resolved in another process directory | parse with an absolute path |
| missing source/reference in final root | launcher assumed pre-created assets | self-contained directory/source/reference preparation plus preflight |
| original far too quiet | “reduce by 25%” confused with “set to 25%” | use gain 0.75 for a 25% reduction |

## Acceptance checklist for every future run

- `CUDA available: False` is present in the log.
- Source URL, model snapshot, clone mode, steps, CFG, thread count and cache length are recorded.
- Every expected segment has attempts, selected candidate, fitted WAV and report record.
- No segment is hidden behind an extreme tempo correction.
- No short candidate is slowed merely to fill a timing window.
- Final audio duration matches the source timeline.
- Russian ASR contains the complete intended translation.
- Scripture references and theological terms are verified.
- First and last words are not clipped.
- Pause-then-chewing restart is absent.
- Russian-only output is reviewed before judging mix-induced echo.
- Original English uses one explicitly documented constant gain.
- Preflight, synthesis and master reports are retained.
- Failures are appended here with exact traceback and resolution.
