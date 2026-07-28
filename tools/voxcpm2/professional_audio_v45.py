#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dub Studio professional audio policy v4.5."""
from __future__ import annotations

import hashlib, json, math, re, shutil, subprocess, tempfile
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

from tools.voxcpm2 import dub_quality_v4
from tools.voxcpm2 import generic_short_production as pipeline
from tools.voxcpm2 import semantic_tts_guard_v4

POLICY = "professional-audio-v4.5"
RENDERER = "voxcpm2_quality_v45_renderer.py"
MASTER = "master_quality_v45.py"
_ORIGINAL_VERIFY = semantic_tts_guard_v4.verify_timeline_v4
_INSTALLED = False
_SENTENCE = re.compile(r"(?<=[.!?…;:])\s+")


def log(text: str) -> None:
    print(f"[DUB-PRO-V4.5] {text}", flush=True)


def words(text: str) -> int:
    return max(1, len(re.findall(r"\w+", str(text or ""), re.UNICODE)))


def split_balanced(text: str, count: int) -> list[str]:
    tokens = str(text or "").split()
    count = min(max(1, count), max(1, len(tokens)))
    return [
        " ".join(tokens[round(i * len(tokens) / count):round((i + 1) * len(tokens) / count)]).strip()
        for i in range(count)
        if tokens[round(i * len(tokens) / count):round((i + 1) * len(tokens) / count)]
    ]


def split_text(text: str, duration: float, max_seconds: float = 5.4) -> list[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    required = max(1, math.ceil(max(0.1, duration) / max_seconds))
    parts = [x.strip() for x in _SENTENCE.split(text) if x.strip()]
    if len(parts) < required:
        parts = split_balanced(text, required)
    total = sum(words(x) for x in parts)
    result: list[str] = []
    for part in parts:
        result += split_balanced(part, max(1, math.ceil(duration * words(part) / max(1, total) / max_seconds)))
    return result


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _legacy_window(item: dict[str, Any], delay: float) -> tuple[float, float]:
    start = float(item.get("original_srt_start", item.get("start", 0)) or 0)
    end = float(item.get("source_end", 0) or 0)
    if end <= start:
        end = float(item.get("end", start) or start) + max(delay, int(item.get("start_delay_ms", 0) or 0) / 1000)
    return max(0.0, start), max(start + 0.35, end)


def migrate_legacy_audio_repair(root: Path, request: dict[str, Any]) -> bool:
    repair_path, segments_path = root / "input" / "audio_repair.json", root / "segments_ru_final.json"
    if not repair_path.is_file() or not segments_path.is_file():
        return False
    repair = json.loads(repair_path.read_text(encoding="utf-8-sig"))
    old = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    if not isinstance(repair, dict) or not repair.get("repair_all") or not isinstance(old, list) or not old:
        return False
    delay_ms = max(0, int(request.get("russian_delay_ms") or 420)); delay = delay_ms / 1000
    if not any(_legacy_window(x, delay)[1] - _legacy_window(x, delay)[0] > 6.2 or x.get("quality_timing") != "global-delay-v4.5" for x in old):
        return False

    new: list[dict[str, Any]] = []
    for old_index, item in enumerate(old, 1):
        start, end = _legacy_window(item, delay); duration = end - start
        parts = split_text(str(item.get("text") or ""), duration)
        if not parts:
            raise RuntimeError(f"Реплика #{old_index} пуста и не может быть мигрирована.")
        weights = [words(x) for x in parts]; total = sum(weights); cursor = start
        for part, weight in zip(parts, weights, strict=True):
            part_end = end if part == parts[-1] else cursor + duration * weight / total
            new.append({
                "id": len(new) + 1, "start": round(cursor, 3), "end": round(part_end, 3),
                "start_delay_ms": delay_ms, "reference_profile": "extended", "tail_guard": 0.18,
                "text": part, "source_end": round(part_end, 3), "source": str(item.get("source") or ""),
                "quality_timing": "global-delay-v4.5", "migrated_from_segment": int(item.get("id") or old_index),
            })
            cursor = part_end
    for i, item in enumerate(new, 1):
        item["id"] = i
        if i == len(new) or i % 5 == 0:
            item["reference_profile"], item["tail_guard"] = "composite", 0.22
    old_words = " ".join(" ".join(str(x.get("text") or "").split()) for x in old).split()
    new_words = " ".join(" ".join(str(x.get("text") or "").split()) for x in new).split()
    if old_words != new_words:
        raise RuntimeError("Миграция изменила русский текст; операция остановлена.")

    backup = root / "segments_ru_final.pre_v45.json"
    if not backup.exists(): shutil.copy2(segments_path, backup)
    segments_path.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    repair.update(segment_ids=[x["id"] for x in new], segments_sha256=sha256(segments_path), migration=POLICY)
    repair_path.write_text(json.dumps(repair, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = root / "output" / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if isinstance(manifest, dict):
            manifest.update(segments=len(new), audio_segmentation=POLICY, legacy_segments_backup=str(backup))
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"legacy timing migrated: {len(old)} -> {len(new)} segments; text preserved")
    return True


def _shifted(groups: list[dict[str, Any]], texts: Iterable[str], *, delay_ms: int, duration: float, direct: bool):
    delay = max(0, int(delay_ms)) / 1000; result = []; subtitles = []; texts = list(texts)
    if len(groups) != len(texts): raise RuntimeError("Количество окон и реплик не совпадает.")
    for i, (group, text) in enumerate(zip(groups, texts, strict=True), 1):
        start = max(0.0, float(group["start"])); source_end = min(duration, float(group["end"]))
        end = min(source_end, max(start + 0.35, duration - delay))
        profile = "composite" if i == len(groups) or i % 5 == 0 else "extended"
        item = {
            "id": i, "start": round(start, 3), "end": round(end, 3), "start_delay_ms": int(delay * 1000),
            "reference_profile": profile, "tail_guard": 0.22 if profile == "composite" else 0.18,
            "text": str(text).strip(), "source_end": round(source_end, 3),
            "source": str(group.get("source") or group.get("english") or ""), "quality_timing": "global-delay-v4.5",
        }
        if direct: item.update(text_policy="verbatim_user_srt", original_srt_start=round(start, 3), timing_window_expanded=False)
        result.append(item)
        subtitles.append(pipeline.Cue(min(duration, start + delay), min(duration, source_end + delay), str(text).strip()))
    return result, subtitles


def build_render_segments_v45(groups, translations, *, delay_ms, duration):
    return _shifted(groups, (x["russian"] for x in translations), delay_ms=delay_ms, duration=duration, direct=False)


def build_direct_segments_v45(groups, *, delay_ms, duration):
    return _shifted(groups, (x["source"] for x in groups), delay_ms=delay_ms, duration=duration, direct=True)


def pitch_profile(samples: np.ndarray, sr: int) -> dict[str, float]:
    audio = np.asarray(samples, np.float32).reshape(-1); frame = max(320, int(sr * .04)); hop = max(160, int(sr * .02))
    if len(audio) < frame: return {"voiced_ratio": 0.0, "f0_median": 0.0, "f0_p90": 0.0}
    rms = np.array([np.sqrt(np.mean(audio[p:p+frame] ** 2) + 1e-12) for p in range(0, len(audio)-frame+1, hop)])
    threshold = max(float(np.percentile(rms, 35)) * 1.7, 10 ** (-40/20)); values = []
    lo, hi = max(2, int(sr/300)), min(frame-3, int(sr/65))
    for j, p in enumerate(range(0, len(audio)-frame+1, hop)):
        if rms[j] < threshold: continue
        x = audio[p:p+frame].astype(np.float64); x -= x.mean(); x *= np.hanning(frame)
        ac = np.correlate(x, x, "full")[frame-1:]
        if ac[0] <= 1e-9: continue
        lag = lo + int(np.argmax(ac[lo:hi+1]))
        if ac[lag] / ac[0] >= .30: values.append(sr / lag)
    if not values: return {"voiced_ratio": 0.0, "f0_median": 0.0, "f0_p90": 0.0}
    arr = np.asarray(values)
    return {"voiced_ratio": len(values)/max(1, len(rms)), "f0_median": float(np.median(arr)), "f0_p90": float(np.percentile(arr, 90))}


def activity_stats(samples: np.ndarray, sr: int) -> dict[str, float]:
    audio = np.asarray(samples, np.float32).reshape(-1); frame = max(160, int(sr*.02)); hop = max(80, int(sr*.01))
    levels = np.array([np.sqrt(np.mean(audio[p:p+frame] ** 2)+1e-12) for p in range(0, max(1, len(audio)-frame+1), hop)])
    active = levels >= max(10 ** (-42/20), float(levels.max()) * .055); ids = np.where(active)[0]; max_gap = 0.0
    if len(ids) > 1:
        run = 0
        for value in active[ids[0]:ids[-1]+1]:
            if value: max_gap, run = max(max_gap, run*hop/sr), 0
            else: run += 1
    return {"active_ratio": float(active.mean()), "max_internal_gap": float(max_gap),
            "rms_dbfs": 20*math.log10(math.sqrt(float(np.mean(audio**2))+1e-12)+1e-12),
            "peak_dbfs": 20*math.log10(float(np.max(np.abs(audio)))+1e-12)}


def _decode(source: Path, output: Path):
    cmd = ["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(source),"-vn","-ac","1","-ar","16000",
           "-af","highpass=f=60,lowpass=f=7600,afftdn=nr=7:nf=-40:tn=1","-c:a","pcm_f32le",str(output)]
    if subprocess.run(cmd, check=False).returncode != 0: raise RuntimeError("Не удалось подготовить голосовой референс.")
    return sf.read(output, dtype="float32")


def build_reference_v45(source: Path, intervals: list[tuple[float,float]], output: Path, *, target_seconds: float) -> None:
    runs = []
    for start, end in sorted((max(0.,float(a)),max(0.,float(b))) for a,b in intervals if float(b)-float(a)>=.35):
        if runs and start-runs[-1][1] <= .28: runs[-1][1] = max(runs[-1][1], end)
        else: runs.append([start,end])
    if not runs: raise RuntimeError("Нет пригодных интервалов для voice reference.")
    target = max(6., min(float(target_seconds), 10.)); window = min(5., max(3.2, target/2))
    with tempfile.TemporaryDirectory(prefix="dub-ref-v45-") as raw:
        whole, sr = _decode(source, Path(raw)/"source.wav"); sr = int(sr); candidates = []
        for a,b in runs:
            length=b-a
            if length<2.2: continue
            w=min(window,length); steps=max(1,int((length-w)/.75)); starts=[a+i*(length-w)/steps for i in range(steps+1)]
            for start in starts:
                clip=np.asarray(whole[int(start*sr):int(min(b,start+w)*sr)],np.float32)
                if len(clip)<sr*2: continue
                pitch=pitch_profile(clip,sr); act=activity_stats(clip,sr)
                score=pitch["f0_median"]*.45+pitch["f0_p90"]*.18+act["max_internal_gap"]*60+abs(act["active_ratio"]-.72)*45
                if pitch["voiced_ratio"]<.16: score+=120
                candidates.append((score,start,min(b,start+w),clip,{**pitch,**act}))
        if not candidates: raise RuntimeError("Не найден устойчивый голосовой референс.")
        selected=[]; total=0
        for item in sorted(candidates,key=lambda x:x[0]):
            if any(min(item[2],x[2])-max(item[1],x[1])>.75 for x in selected): continue
            selected.append(item); total+=item[2]-item[1]
            if total>=target: break
        parts=[x[3] for x in selected]; audio=dub_quality_v4._crossfade(parts,sr)[:int(target*sr)]
        rms=math.sqrt(float(np.mean(audio**2))+1e-12); peak=float(np.max(np.abs(audio)))+1e-12
        audio=np.clip(audio*min(10**(-24/20)/rms,10**(-3/20)/peak,10**(5/20)),-.999,.999).astype(np.float32)
        fade=min(int(sr*.025),len(audio)//8)
        if fade > 1:
            ramp=np.linspace(0,1,fade,dtype=np.float32); audio[:fade]*=ramp; audio[-fade:]*=ramp[::-1]
        output.parent.mkdir(parents=True,exist_ok=True); sf.write(output,audio,sr,subtype="PCM_24")
    report={"policy":POLICY,"selected":[{"start":round(x[1],3),"end":round(x[2],3),"score":round(x[0],3),**{k:round(float(v),4) for k,v in x[4].items()}} for x in selected]}
    output.with_suffix(".selection.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    log("reference calm windows: "+", ".join(f"{x[1]:.2f}-{x[2]:.2f}" for x in selected))


def verify_timeline_v45(timeline: Path, segments: list[dict[str,Any]], report_path: Path):
    failed, report = _ORIGINAL_VERIFY(timeline, segments, report_path); failed=set(map(int,failed))
    checks={int(x.get("id")):x for x in report.get("segments",[]) if isinstance(x,dict) and str(x.get("id","")).isdigit()}
    with tempfile.TemporaryDirectory(prefix="dub-v45-qa-") as raw:
        for item in segments:
            sid=int(item["id"]); delay=max(0,int(item.get("start_delay_ms",0)))/1000; clip=Path(raw)/f"{sid}.wav"
            semantic_tts_guard_v4.legacy._extract_clip(timeline,clip,float(item["start"])+delay,max(.35,float(item["end"])-float(item["start"])))
            samples,sr=semantic_tts_guard_v4.legacy._read_pcm_mono(clip); stats=activity_stats(np.asarray(samples),int(sr))
            allowed=.78 if re.search(r"[.!?…;:]",str(item.get("text") or "")) else .58
            passed=stats["max_internal_gap"]<=allowed and stats["active_ratio"]>=.20
            check=checks.setdefault(sid,{"id":sid,"passed":True}); check["continuity_v45"]={**stats,"max_allowed":allowed,"passed":passed}; check["passed"]=bool(check.get("passed") and passed)
            if not check["passed"]: failed.add(sid)
    result=sorted(failed); report.update(professional_audio_policy=POLICY,passed=not result,failed_segment_ids=result)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); return result,report


def install() -> None:
    global _INSTALLED
    if _INSTALLED: return
    dub_quality_v4.build_reference_v4=build_reference_v45; dub_quality_v4.build_render_segments_v4=build_render_segments_v45
    semantic_tts_guard_v4._QUALITY_RENDERER=RENDERER; semantic_tts_guard_v4._QUALITY_MASTER=MASTER
    semantic_tts_guard_v4.verify_timeline_v4=verify_timeline_v45
    try:
        from tools.voxcpm2 import generic_direct_checked_runtime
        generic_direct_checked_runtime.build_direct_segments_safe=build_direct_segments_v45
    except Exception as exc: log(f"direct patch warning: {exc}")
    _INSTALLED=True; log("installed calm references, global delay, short segments and continuity QA")
