# Shorts static-slide blur audit — 2026-07-23

## Operator-confirmed policy

- Moving/live footage keeps the existing `crop_zoom` default.
- A static cover, frozen slide, or near-static design frame must be shown whole and centered on the existing blurred 9:16 background.
- Detection uncertainty, ffmpeg failure, missing metrics, or insufficient evidence must keep `crop_zoom`; automatic blur is not allowed to become the universal default.
- `cropdetect` remains ignored after the existing static-slide switch so dark design blocks cannot be mistaken for black bars.

## Production evidence

The marathon log showed both paths working, but one static-looking cover was classified as moving and rendered with `crop_zoom(medium)`. Other covers from the same batch were correctly switched to `full_frame_blur`. The old detector used one strict `freezedetect=n=-60dB:d=2` probe, so codec noise, grain, or small decorative motion could create a false negative.

## Fix

`services.shorts_static_runtime` replaces the effective `_is_static_video` helper before Shorts and montage modules import it.

The new detector:

1. skips the first 0.75 seconds of the selected fragment to avoid cut/fade noise;
2. analyses two separated 8-second windows; both must look static, so a long opening title card cannot force blur onto later moving footage;
3. uses a central crop, where a real speaker's face, mouth and gestures occupy more of the analysed frame;
4. downsamples with the `area` scaler to 96×96 `yuv420p`, suppressing codec noise, film grain, and tiny decorative particles while keeping `signalstats` on a widely supported format;
5. combines dominant `freezedetect` coverage with `signalstats` YDIF median/p90/p98 motion evidence;
6. requires enough samples and logs every probe's metrics and decision;
7. caches bounded results by file fingerprint and timestamp;
8. fails safely to the existing crop mode on every error or uncertain result.

Environment tuning is optional; defaults are production-ready:

- `SHORTS_STATIC_BLUR_AUTO=1`
- `SHORTS_STATIC_MULTI_PROBE=1`
- `SHORTS_STATIC_PROBE_SECONDS=8`
- `SHORTS_STATIC_PROBE_OFFSET=0.75`
- `SHORTS_STATIC_SECOND_PROBE_OFFSET=12`
- `SHORTS_STATIC_FREEZE_NOISE_DB=-50`
- `SHORTS_STATIC_FREEZE_SECONDS=1.5`
- `SHORTS_STATIC_FREEZE_RATIO_MIN=0.86`
- `SHORTS_STATIC_YDIF_MEDIAN_MAX=0.55`
- `SHORTS_STATIC_YDIF_P90_MAX=1.60`
- `SHORTS_STATIC_YDIF_P98_MAX=3.50`

No new `.env` lines are required.
