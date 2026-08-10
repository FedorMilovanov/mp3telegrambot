# Translation Editorial Review v1

This workflow treats Yandex LiveDub as an editorial source that may be repaired surgically instead of requiring a perfect machine translation.

## Evidence boundary

One review pack binds:

- the exact translated source-video SHA-256 and local path;
- the original source SRT;
- a full Russian `faster-whisper` `large-v3` SRT of what is actually heard;
- optional Russian word-level Whisper timestamps;
- Factory Shorts and 5–15 minute candidate metadata;
- one deterministic `review_pack_id`.

The ZIP intentionally does **not** contain the video bytes. It is small enough to upload to an external editor such as ChatGPT or Gemini while later FFmpeg execution remains bound to the local source SHA-256.

## Editorial verdicts

`keep` means the translation may be slightly rough but preserves the intended meaning. Do not repair style merely because another Russian wording would be prettier.

`repair` means a localized defect can be corrected without changing the speaker's argument.

`reject` means the full sermon or candidate must not be released from this translation.

Issue severity is `roughness`, `minor`, `major`, or `critical`.

## Repair actions

Version 1 recognizes four editorial actions:

- `drop_span` — remove a short bad span while preserving the surrounding argument;
- `mute_span` — keep video timing but silence a technical/audio defect;
- `borrow_span` — identify a same-voice donor span elsewhere in the same translated source;
- `reject_region` — record that the region must not be published/reused.

Only `drop_span` and `mute_span` are executable automatically in v1. `borrow_span` is deliberately review-only: donor discovery is deterministic, but inserting borrowed speech can create false phrasing or mismatched prosody/background audio and therefore requires a later explicitly approved repair pass.

## PowerShell workflow

From the repository root:

```powershell
python .\tools\translation_editorial.py prepare `
  --url "https://www.youtube.com/watch?v=VIDEO_ID" `
  --source-video ".\downloads\VIDEO_ID_factory_source.mp4" `
  --media-id "VIDEO_ID" `
  --title "Title" `
  --performer "Speaker" `
  --duration 3600 `
  --candidates ".\factory-plan.json" `
  --output-dir ".\downloads\editorial"
```

The command downloads the original SRT, transcribes the complete translated source with Russian Whisper `large-v3`, and emits:

- `VIDEO_ID_translation_editorial_v1.zip`;
- `VIDEO_ID_review_template.json`.

Upload the ZIP to the editor. The returned `review.json` must retain the exact `review_pack_id`.

Validate it before any media operation:

```powershell
python .\tools\translation_editorial.py validate `
  --pack ".\downloads\editorial\VIDEO_ID_translation_editorial_v1.zip" `
  --review ".\review.json"
```

Apply only the safe v1 repairs:

```powershell
python .\tools\translation_editorial.py repair `
  --pack ".\downloads\editorial\VIDEO_ID_translation_editorial_v1.zip" `
  --review ".\review.json" `
  --output ".\downloads\VIDEO_ID_editorial_clean.mp4"
```

The repair command refuses to run when:

- the review targets another pack;
- the source-video bytes have changed;
- the full sermon is rejected;
- the review contains unresolved `borrow_span` or `reject_region` actions;
- FFmpeg cannot prove a usable output.

## Same-voice donor discovery

To search the actual Russian Whisper transcript without changing media:

```powershell
python .\tools\translation_editorial.py donors `
  --russian-srt ".\russian_whisper.srt" `
  --phrase "дела" `
  --exclude-start 120.4 `
  --exclude-end 121.0
```

The result is only a list of grounded cue candidates with timestamps and heard text. It is not permission to splice speech automatically.

## Intended next integration

Factory can call this same service after it has a durable Yandex source and candidate plan, then send the small review ZIP to Telegram. A later release bridge may convert reviewed final media and editorial metadata into the guarded `video-channel-manager` exchange/release path. AI remains an editor, not a provider executor.
