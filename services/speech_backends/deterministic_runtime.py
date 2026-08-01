"""Small deterministic PCM renderer used to prove multi-backend contracts in CI."""
from __future__ import annotations

import argparse
import json
import math
import wave
from array import array
from pathlib import Path

RUNTIME_POLICY = "deterministic-ci-pcm-renderer-v1"


def _segment_texts(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    items = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise RuntimeError("segments JSON должен содержать список.")
    return [
        str(item.get("text") or "")
        for item in items
        if isinstance(item, dict)
    ]


def render_timeline(
    *,
    segments_json: Path,
    output: Path,
    duration: float,
    sample_rate: int = 22_050,
) -> Path:
    if sample_rate <= 0:
        raise ValueError("sample_rate должен быть > 0.")
    duration = max(0.25, min(float(duration), 60.0))
    texts = _segment_texts(Path(segments_json))
    seed = sum(
        (index + 1) * sum(ord(char) for char in text)
        for index, text in enumerate(texts)
    )
    frequency = 180.0 + float(seed % 240)
    total = max(1, int(round(duration * sample_rate)))
    pcm = array("h")
    for index in range(total):
        seconds = index / sample_rate
        envelope = min(1.0, index / max(1, sample_rate // 50))
        envelope *= min(1.0, (total - index) / max(1, sample_rate // 50))
        value = int(2200.0 * envelope * math.sin(2.0 * math.pi * frequency * seconds))
        pcm.append(value)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with wave.open(str(temporary), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    temporary.replace(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic CI speech renderer")
    parser.add_argument("--segments-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--video-duration", type=float, required=True)
    parser.add_argument("--sample-rate", type=int, default=22_050)
    args, _unknown = parser.parse_known_args()
    render_timeline(
        segments_json=args.segments_json,
        output=args.output,
        duration=args.video_duration,
        sample_rate=args.sample_rate,
    )
    print(
        json.dumps(
            {
                "policy": RUNTIME_POLICY,
                "output": str(args.output),
                "sample_rate": args.sample_rate,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
