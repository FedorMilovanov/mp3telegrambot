# Translation Editorial Composition v1

This stage begins **after** translation review and, when needed, after the full Yandex master has been repaired. It is deliberately separate from semantic QA: review decides what speech is trustworthy; composition decides how approved speech may be assembled into publishable media.

## Supported outputs

A single exact cleaned source may produce:

- `full` — a reviewed long-form master that still retains at least 85% of the source and reaches the beginning/end of the sermon within a small edge allowance;
- `excerpt` — a continuous or editorial 2–20 minute extract;
- `short` — a 10–180 second Short.

Every output is assembled from explicit source-time segments. The plan carries the exact source path, byte count, SHA-256, probed duration, review/repair provenance and optional future release metadata. The self-binding `composition_id`, however, is deliberately the **media assembly identity**: it binds source bytes, review/repair evidence and media-producing segment decisions, not machine-local paths or publication copy.

Two assembly modes exist:

- `continuous` — exactly one source interval;
- `editorial_sequence` — two or more source intervals, always kept in original chronological order and requiring a meaningful editorial rationale.

The chronological rule is intentional: an editor may combine a problem, example and conclusion from different parts of one sermon, but the renderer must not silently reorder the speaker's argument. Non-finite timestamps, overlaps, out-of-source spans, unsafe/colliding output IDs and implausible durations fail validation before FFmpeg.

## PowerShell workflow

Bind an empty plan to the exact cleaned source and the exact review evidence that approved it:

```powershell
python .\tools\translation_editorial_composition.py init `
  --source-video ".\downloads\VIDEO_ID_editorial_clean.mp4" `
  --title "Sermon title" `
  --performer "Speaker" `
  --review ".\review.json" `
  --review-pack ".\VIDEO_ID_translation_editorial_v1_a1b2c3d4e5f6.zip" `
  --project-key "sermon-project" `
  --youtube-account-alias "legendary-poet" `
  --youtube-channel-id "UC_EXACT" `
  --output ".\composition.json"
```

When both `--review` and `--review-pack` are supplied, the CLI re-verifies the ZIP evidence, validates the review against that exact pack, takes `review_pack_id` automatically and hashes the exact `review.json`. There is no reason to retype the ID. `--review-pack-id` remains only as a compatibility/manual option for cases where the exact ZIP is intentionally unavailable; if it disagrees with `review.json`, `init` fails.

The source duration is always taken from the actual media probe. `--duration` remains available only as an optional expected value; if supplied and it disagrees with the probe, `init` fails instead of freezing a guessed duration into provenance.

If an already verified repaired clean master is moved to a different local directory, its historical repair sidecar does not need to be rewritten merely because the path changed. When that relocated path is supplied explicitly, the verifier treats the stored path as history and accepts the relocated file only after the exact stored byte count, SHA-256 and media duration all re-verify. Changed bytes still fail closed.

The editor fills `pieces`. Example:

```json
{
  "piece_id": "short-faith-and-works",
  "kind": "short",
  "assembly_mode": "editorial_sequence",
  "editorial_rationale": "The three passages continue the same argument in original order.",
  "segments": [
    {"start_seconds": 310.2, "end_seconds": 326.1},
    {"start_seconds": 1142.5, "end_seconds": 1165.0},
    {"start_seconds": 2480.8, "end_seconds": 2502.6}
  ],
  "publication": {
    "title": "Вера и дела",
    "description": "...",
    "hashtags": ["#Вера", "#Дела"],
    "playlist": "Проповеди"
  }
}
```

`piece_id` is deliberately restricted to an already filesystem-safe **portable** value. Validation is conservative across platforms rather than depending on the machine doing the render: Windows device stems such as `CON`, `PRN`, `AUX`, `NUL`, `COM1`…`COM9` and `LPT1`…`LPT9` are rejected on every OS, and output names are compared after Unicode NFC normalization plus case folding. Thus IDs such as `Short` and `short` cannot become two apparently distinct plans that collide on a case-insensitive filesystem.

After an edit that changes the actual media assembly — source identity, repair evidence, segment boundaries/order, kind or assembly rationale — recompute the media ID:

```powershell
python .\tools\translation_editorial_composition.py refresh-id `
  --plan ".\composition.json"
```

Changing only title, description, hashtags, playlist, schedule or release target does **not** change `composition_id` and therefore does not require re-encoding an identical MP4. Those release decisions are bound later by `handoff_id`.

Then validate and render:

```powershell
python .\tools\translation_editorial_composition.py validate `
  --plan ".\composition.json"

python .\tools\translation_editorial_composition.py render `
  --plan ".\composition.json" `
  --output-dir ".\downloads\editorial_outputs"
```

## Render and resume guarantees

Before FFmpeg starts, the renderer verifies the exact source byte count, SHA-256 and measured video+audio duration. If the source is a repaired master, the service itself also reloads and verifies the repair-provenance sidecar, its SHA-256, `repair_result_id`, review pack ID and review SHA; this cannot be bypassed by calling the Python renderer directly instead of the CLI.

Each new output is rendered to a same-directory temporary file with FFmpeg no-overwrite mode, probed again, and only then published under its final name. Each accepted piece receives an immutable `.provenance.json` sidecar containing:

- exact composition ID;
- exact source SHA-256;
- all source segment timestamps and media-producing piece metadata;
- final output path, SHA-256, byte count and measured duration;
- deterministic `result_id`.

A rerun may reuse an existing piece **only** when both the MP4 and sidecar exist and all media identities re-verify. A partial pair, changed media composition, changed source, changed output bytes, stale `result_id`, or path mismatch blocks resume. Publication-only changes may reuse the verified MP4 because they do not alter the media assembly.

The rendered video is H.264/AAC with `yuv420p` for broad player compatibility. Existing outputs are never silently overwritten.

## Release handoff

A successful render creates a content-addressed release handoff such as:

```text
editorial-release-handoff_0123456789abcdef...<64 hex total>.json
```

The 64-hex suffix is the complete digest portion of `handoff_id`. Before handoff creation the code reloads every provenance sidecar and rehashes the real output bytes rather than trusting the in-memory result list. The handoff carries:

- output path, SHA-256, byte count and measured duration;
- provenance path and provenance SHA-256;
- `result_id`;
- source segments and assembly mode;
- current publication metadata;
- current release target;
- exact review/repair source provenance;
- exact media `composition_id`.

`handoff_id` binds this complete release state. Therefore changing title/description/playlist/schedule/channel changes the handoff identity even when the underlying `composition_id` and rendered MP4 correctly remain unchanged. Because the filename is derived from that identity, multiple publication-only revisions can coexist safely in one output directory; a later card no longer collides with or overwrites an earlier handoff for the same MP4.

Missing, extra, duplicate or tampered rendered results fail closed. An already existing handoff may be reused only when it is byte-for-byte the same logical handoff; a different state receives a different content-addressed filename rather than overwriting the old state.

The handoff is explicitly:

```json
"provider_write_authorized": false
```

It is an editorial/release input, not permission to upload. The current `video-channel-manager` guarded YouTube executor remains a separate implementation and authorization boundary. If only part of a release target is supplied, validation requires at least the canonical `project_key` and exact YouTube channel ID rather than carrying an ambiguous target forward.

## Advanced donor speech

Same-voice donor discovery from Translation Editorial Review v1 remains available, but donor insertion is not part of automatic composition v1. A borrowed word or phrase must be separately approved because prosody, surrounding phonemes and background audio can make a technically valid splice editorially false or audibly poor.
