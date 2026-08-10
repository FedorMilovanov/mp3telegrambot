# Translation Editorial Composition v1

This stage begins **after** translation review and, when needed, after the full Yandex master has been repaired. It is deliberately separate from semantic QA: review decides what speech is trustworthy; composition decides how approved speech may be assembled into publishable media.

## Supported outputs

A single exact cleaned source may produce:

- `full` — a reviewed long-form master;
- `excerpt` — a continuous or editorial 2–20 minute extract;
- `short` — a 10–180 second Short.

Every output is assembled from explicit source-time segments. The plan binds the exact source path, SHA-256, duration, optional review IDs, and optional future YouTube target identity.

Two assembly modes exist:

- `continuous` — exactly one source interval;
- `editorial_sequence` — two or more source intervals, always kept in original chronological order and requiring an explicit editorial rationale.

The chronological rule is intentional: an editor may combine a problem, example and conclusion from different parts of one sermon, but the renderer must not silently reorder the speaker's argument.

## PowerShell workflow

Bind an empty plan to the exact cleaned source:

```powershell
python .\tools\translation_editorial_composition.py init `
  --source-video ".\downloads\VIDEO_ID_editorial_clean.mp4" `
  --duration 3594.2 `
  --title "Sermon title" `
  --performer "Speaker" `
  --review ".\review.json" `
  --review-pack-id "sha256:EXACT_REVIEW_PACK_ID" `
  --project-key "sermon-project" `
  --youtube-account-alias "legendary-poet" `
  --youtube-channel-id "UC_EXACT" `
  --output ".\composition.json"
```

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

After any editorial change recompute the self-binding ID:

```powershell
python .\tools\translation_editorial_composition.py refresh-id `
  --plan ".\composition.json"
```

Then validate and render:

```powershell
python .\tools\translation_editorial_composition.py validate `
  --plan ".\composition.json"

python .\tools\translation_editorial_composition.py render `
  --plan ".\composition.json" `
  --output-dir ".\downloads\editorial_outputs"
```

## Render guarantees

Before FFmpeg starts, the renderer verifies the exact source SHA-256 and media probe duration. Each output is encoded from the approved source segments, probed again, and receives an immutable `.provenance.json` sidecar containing:

- exact composition ID;
- exact source SHA-256;
- all source segment timestamps;
- piece metadata;
- output path, SHA-256, size and measured duration.

Existing outputs are never overwritten silently.

## Release handoff

A successful render also creates:

```text
editorial-release-handoff.json
```

It carries the rendered media identities plus publication metadata and source-segment provenance for later import into `video-channel-manager`.

The handoff is explicitly:

```json
"provider_write_authorized": false
```

It is an editorial/release input, not permission to upload. The current `video-channel-manager` guarded YouTube executor remains a separate implementation and authorization boundary.

## Advanced donor speech

Same-voice donor discovery from Translation Editorial Review v1 remains available, but donor insertion is not part of automatic composition v1. A borrowed word or phrase must be separately approved because prosody, surrounding phonemes and background audio can make a technically valid splice editorially false or audibly poor.
