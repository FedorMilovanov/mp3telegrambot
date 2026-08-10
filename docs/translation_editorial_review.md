# Translation Editorial Review v1

This workflow treats Yandex LiveDub as an editorial source that may be repaired surgically instead of requiring a perfect machine translation.

## Evidence boundary

One review pack binds:

- the exact translated source-video SHA-256, size and durable local path;
- the original source SRT;
- a full Russian `faster-whisper` `large-v3` SRT of what is actually heard;
- optional Russian word-level Whisper timestamps;
- Factory Shorts and long-clip candidate metadata;
- explicit original-vs-translated timeline metadata;
- one deterministic `review_pack_id`.

The ZIP intentionally does **not** contain the video bytes. It is small enough to upload to an editor such as ChatGPT while later FFmpeg execution remains bound to the exact local source bytes.

For Factory, each distinct translated source is preserved under `downloads/translation_editorial/<media_id>/` with a SHA-derived filename instead of relying on the short-lived `*_factory_source.*` trim cache. The code prefers a hard link and falls back to an exact copy. A later Yandex result for the same video ID but different bytes therefore receives a different durable source rather than overwriting or conflicting with the evidence used by an older review.

Each ZIP filename contains a prefix of its own `review_pack_id`, for example:

```text
VIDEO_ID_translation_editorial_v1_a1b2c3d4e5f6.zip
```

A later run with different evidence creates another file instead of replacing the first. Before a verified ZIP is deeply read, the loader bounds physical/member/uncompressed sizes, rejects duplicate/nested/encrypted members, and validates canonical manifest/candidate shapes. It then rechecks transcript byte counts and SHA-256 values, exact `candidates.json`, globally unique candidate IDs, canonical transcript files/roles, ZIP-member set, editor-facing review contract/instructions and deterministic `review_pack_id`. Legacy PR #113 v1 packs without timeline metadata remain verifiable against their original identity and original known instruction text.

For Yandex Factory jobs the review pack is enabled by default. It is generated after the normal Shorts/long-clip render so a review-pack failure never cancels already produced videos:

```env
SHORTS_FACTORY_EDITORIAL_REVIEW_PACK=1
```

The optional automatic semantic auditor is off by default:

```env
SHORTS_FACTORY_EDITORIAL_GEMINI=0
```

When enabled it performs a full-sermon review using the exact model `gemini-3.6-flash` with `thinking_level=high`. The default budget is one attempt; an explicit override is clamped to two. It has no light-model fallback and does not apply its own edit decisions automatically. Machine-local source paths are removed from the model prompt.

## Timeline rule

The original SRT and heard Russian SRT do not necessarily share the same clock. In the normal LiveDub path the Russian speech is deliberately delayed, and Factory candidates receive the configured translation shift as well. `manifest.json.timeline` records that relationship.

The editor must align by semantic sequence plus the timeline evidence, not by assuming that cue 100 in the source corresponds to cue 100 in Russian or that the same clock second means the same spoken phrase. All executable issue timestamps target the Russian/translated-video timeline.

## Editorial verdicts

`keep` means the translation may be slightly rough but preserves the intended meaning. A `keep` verdict cannot carry repair actions.

`repair` means at least one localized defect has been identified and can be corrected without changing the speaker's argument.

`reject` means the full sermon or candidate must not be released from this translation.

Issue severity is `roughness`, `minor`, `major`, or `critical`. Every Factory candidate must receive exactly one review. Candidate-level issue timestamps must point inside that candidate rather than somewhere else in the sermon.

## Repair actions

Version 1 recognizes four editorial actions:

- `drop_span` — remove a short bad span while preserving the surrounding argument;
- `mute_span` — keep video timing but silence a technical/audio defect;
- `borrow_span` — identify a same-voice donor span elsewhere in the same translated source;
- `reject_region` — record that the region must not be published/reused.

Only `drop_span` and `mute_span` are executable automatically in v1. `borrow_span` is deliberately review-only: donor discovery is deterministic, but inserting borrowed speech can create false phrasing or mismatched prosody/background audio and therefore requires a later explicitly approved repair pass.

`drop_span` is intentionally bounded as a **surgical** operation rather than a general-purpose edit decision. One automatic drop may remove at most 8 seconds. Across the full sermon, merged automatic drops may remove at most `min(60 seconds, max(5 seconds, 2% of the probed source duration))`. Overlapping drops count only once after merging. `mute_span` does not shorten the argument timeline and is not charged to this deletion budget. The review validator applies the limit before a plan can be approved, and `apply_safe_repairs()` independently recomputes it from the real probed media duration before FFmpeg, so a hand-edited review or direct service call cannot bypass the destructive-removal limit.

## PowerShell workflow

From the repository root:

```powershell
python .\tools\translation_editorial.py prepare `
  --url "https://www.youtube.com/watch?v=VIDEO_ID" `
  --source-video ".\downloads\VIDEO_ID_factory_source.mp4" `
  --media-id "VIDEO_ID" `
  --title "Title" `
  --performer "Speaker" `
  --candidates ".\factory-plan.json" `
  --output-dir ".\downloads\editorial"
```

The exact pack duration is measured from the real video+audio source. `--duration` is optional and is now only an expected-value check; when supplied, a meaningful mismatch from the media probe fails instead of freezing a guessed duration into provenance. Manual `media_id` values are normalized before becoming local filenames, malformed candidate JSON fails closed instead of silently dropping entries, and each prepare run uses its own staging directory.

The command downloads the original SRT, transcribes the complete translated source with Russian Whisper `large-v3`, and emits hash-qualified files such as:

```text
VIDEO_ID_translation_editorial_v1_a1b2c3d4e5f6.zip
VIDEO_ID_a1b2c3d4e5f6_review_template.json
```

Upload the exact ZIP to the editor. The returned `review.json` must retain the exact `review_pack_id` from that ZIP.

Validate it before any media operation:

```powershell
python .\tools\translation_editorial.py validate `
  --pack ".\downloads\editorial\VIDEO_ID_translation_editorial_v1_a1b2c3d4e5f6.zip" `
  --review ".\review.json"
```

Apply only the safe v1 repairs:

```powershell
python .\tools\translation_editorial.py repair `
  --pack ".\downloads\editorial\VIDEO_ID_translation_editorial_v1_a1b2c3d4e5f6.zip" `
  --review ".\review.json" `
  --output ".\downloads\VIDEO_ID_editorial_clean.mp4"
```

A successful repair emits both:

```text
VIDEO_ID_editorial_clean.mp4
VIDEO_ID_editorial_clean.editorial-repair.json
```

The sidecar binds the exact `review_pack_id`, exact `review.json` SHA-256, reviewed source SHA-256, exact executable repair spans, merged `drop_span` ranges, final clean-master SHA-256/size/duration and a deterministic `repair_result_id`. It also records how pre-repair review timestamps map onto the cleaned timeline after dropped spans.

The repair command refuses to run when:

- the review targets another pack, the pack instructions/contract changed, or pack evidence was modified;
- the source-video SHA, size or measured duration no longer matches;
- the requested output path is the source path;
- an existing output/provenance pair belongs to different evidence or only one half of the pair exists;
- the full sermon is rejected;
- the review contains unresolved `borrow_span` or `reject_region` actions;
- a repair timestamp is non-finite or outside the source;
- one `drop_span` exceeds 8 seconds or merged automatic drops exceed the source-specific surgical deletion budget;
- FFmpeg output does not pass the final video+audio probe and duration check.

An exact previously verified output+sidecar pair for the same review is reused without a second FFmpeg run. New media is rendered through temporary/no-overwrite paths. Final-path ownership is deliberately conservative: if a late provenance-finalization race or failure leaves only one half of the final output/provenance pair, the CLI does **not** blindly unlink that final path because it may belong to another concurrent process. The next run detects the partial pair and fails closed for explicit operator resolution instead of risking data loss.

## Same-voice donor discovery

To search the actual Russian Whisper transcript without changing media:

```powershell
python .\tools\translation_editorial.py donors `
  --russian-srt ".\russian_whisper.srt" `
  --phrase "дела" `
  --exclude-start 120.4 `
  --exclude-end 121.0
```

The result is only a list of grounded cue candidates with timestamps and heard text. Exact phrase boundaries are used, so a requested word is not accepted merely because its letters occur inside another word. It is not permission to splice speech automatically.

The Russian review transcript is evidence: only whitespace is normalized after Whisper. It is not passed through the normal subtitle typo/style postprocessor, because that could conceal the exact word the reviewer is supposed to inspect.

## Release boundary

This repository owns translation QA and local deterministic media repair. Composition may later turn a reviewed source into full/excerpt/Short artifacts, and a provider-inert handoff may carry those exact hashes to `video-channel-manager`. AI remains an editor: it does not receive YouTube OAuth and it does not publish provider mutations directly.
