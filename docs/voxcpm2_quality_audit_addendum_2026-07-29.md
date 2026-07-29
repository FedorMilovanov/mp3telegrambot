# VoxCPM2 Max-Quality Audit Addendum — 2026-07-29

This addendum records high-value defects found after the original 90-check,
50-source audit and the exact production decisions made from them.

## 1. Final AAC is the release artifact, not the PCM master

**Finding.** A successful two-pass PCM loudness master does not prove that the
AAC stream inside the MP4 still satisfies loudness, true-peak and duration
requirements. Lossy encoding can create inter-sample peak overshoot and packet
priming can change stream duration.

**Production decision.** Both finished MP4 files are measured again after AAC:

- readable video and audio streams;
- AAC codec, 48 kHz, stereo;
- audio duration and container duration versus source;
- integrated LUFS;
- true peak;
- report persisted before any failure is raised.

**Release limits.** -16 LUFS target ±0.9 LU, final AAC not above -1.0 dBTP,
source-duration delta not above 0.10 s.

**Implementation.** `tools/voxcpm2/final_media_qa.py` and
`master_work/final_media_verification.json`.

## 2. FFmpeg `alimiter` auto-level must be disabled explicitly

**Finding.** FFmpeg documents `alimiter` option `level` as enabled by default.
When enabled it normalizes the output back toward 0 dB. Therefore a filter such
as `alimiter=limit=0.84` is not a trustworthy -1.5 dB ceiling unless
`level=false` is explicit.

The limiter also uses look-ahead and introduces attack-time latency unless
`latency=true` compensates it.

**Production decision.** Every master limiter now uses:

```text
alimiter=limit=<target-linear>:level=false:latency=true
```

The preliminary constant mix uses the same explicit settings. The limiter
ceiling is calculated from the requested dBTP target rather than a hard-coded
0.985 value.

**Primary sources.** FFmpeg filter documentation and current
`libavfilter/af_alimiter.c` option table.

## 3. Controlled expression must be transactional

**Finding.** The expressive builder replaces `composite_reference.wav`. A short,
partial or invalid result could destroy the valid calm fallback before the model
is loaded.

**Production decision.** The calm composite WAV and selection report are backed
up. The expressive replacement is committed only when:

- WAV is readable;
- duration is 5–30 seconds;
- selection report exists and identifies `controlled_expressive`;
- selected windows are present;
- report duration agrees with WAV duration;
- coarse long-term spectral identity remains compatible with the calm
  `extended_reference.wav`.

Any false result, invalid result or Python exception restores both calm files.
All four production routes use the same gate. The direct renderer remains
independent from reference preparation.

**Implementation.** `tools/voxcpm2/controlled_reference_gate.py`.

## 4. Expression cannot silently switch speaker identity

**Finding.** F0 and activity alone do not describe voice colour. An expressive
window can be energetic yet contaminated by music, audience sound or a different
speaker.

**Production decision.** The expressive composite is compared with the calm
identity reference using an 18-band long-term log-frequency energy envelope and
a conservative Bhattacharyya similarity. This is deliberately not presented as
a full speaker-recognition model. It is a low-floor contamination barrier and
the measured similarity is written to the selection report.

The same spectral metric is used as a soft tie-breaker between otherwise valid
VoxCPM candidates. Only gross mismatch is a hard candidate rejection.

## 5. Renderer-policy changes invalidate old checkpoints

**Finding.** Adding timbre-aware candidate selection changes what “accepted
segment” means. Keeping the same checkpoint policy would let an old F0-only
candidate masquerade as a new-quality baseline.

**Production decision.** The direct renderer policy is now
`voxcpm2-direct-max-quality-v3`. Selective `/dubfix` requires every checkpoint
to carry v3 plus the model and reference fingerprints. An old or incomplete
baseline is rejected with a request for one full `/dubfix PROJECT_ID all`.

## 6. Whisper language auto-detection needs a constrained Russian fallback

**Finding.** Auto-language detection can be unstable on a short correct Russian
line. Forcing Russian everywhere would hide genuinely foreign or nonsensical
output such as the earlier `أنا` failure.

**Production decision.** The primary pass remains auto-language. Only a failed
semantic segment receives a second forced-Russian transcription. A confidently
foreign primary result can never be rescued. A forced-Russian rescue also
requires the independent acoustic and timing checks to have passed.

**Implementation policy.** `clean-expression-aware-qa-v3`.

## 7. Progress belongs in one durable Telegram card

**Finding.** Separate 25/50/75% messages create chat spam and make the latest
state harder to find.

**Production decision.** One status message is created per job and edited on
later milestones. Its message ID is persisted in project metadata so the same
card survives a bot restart. A replacement message is created only when Telegram
can no longer edit the old one.

## Verification boundary

Static contracts and synthetic signal tests validate routing, rollback,
fingerprints, filter options and failure semantics. They do not prove local
VoxCPM voice likeness or emotional naturalness. The authoritative acceptance
sequence remains:

1. complete Windows CPU render;
2. inspect reference and candidate reports;
3. listen to Russian-only from start to finish;
4. inspect the emotional arc and segment boundaries;
5. listen to the mixed release version;
6. confirm `final_media_verification.json` passed for both MP4 files.
