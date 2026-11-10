#!/usr/bin/env python3
"""OM-016: A/V sync monitor and drift correction (G5 / AC-2.6).

Builds a lavfi mux with a known audio itsoffset, measures stream start_time
delta via ffprobe, applies inverse itsoffset correction, and re-measures.
Rehearsal mode samples accumulating drift and records residual after correction.

Numbers are measured only; no unpublished product targets.
Citations: ffmpeg itsoffset (ffmpeg-all), lavfi color/sine, ffprobe start_time,
adelay filter (ffmpeg-filters).
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "preview"))
from media_medium import assert_media_medium  # noqa: E402
from run_one_camera import PREVIEW_DIR, RESULTS_DIR, _ffmpeg, _ffprobe  # noqa: E402

OUT_DIR = PREVIEW_DIR / "av_sync"


def probe_av_offset_ms(path: Path) -> dict:
    probe = _ffprobe()
    meta: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists() or not probe:
        meta["ok"] = False
        return meta
    proc = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,start_time,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        meta["ok"] = False
        meta["parse_error"] = True
        return meta
    streams = payload.get("streams") or []
    video_start = None
    audio_start = None
    for stream in streams:
        codec_type = stream.get("codec_type")
        start_raw = stream.get("start_time")
        if start_raw is None:
            continue
        try:
            start = float(start_raw)
        except (TypeError, ValueError):
            continue
        if codec_type == "video" and video_start is None:
            video_start = start
        elif codec_type == "audio" and audio_start is None:
            audio_start = start
    meta["streams"] = streams
    meta["video_start_s"] = video_start
    meta["audio_start_s"] = audio_start
    if video_start is None or audio_start is None:
        meta["ok"] = False
        return meta
    offset_ms = round((audio_start - video_start) * 1000.0, 3)
    meta["offset_ms"] = offset_ms
    meta["ok"] = True
    return meta


def write_offset_mux(
    *,
    ffmpeg: str,
    path: Path,
    offset_s: float,
    duration_s: float,
    width: int,
    height: int,
    fps: int,
) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    # Positive offset_s delays audio relative to video (audio late).
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x1B4D3E:s={width}x{height}:r={fps}:d={duration_s}",
        "-f",
        "lavfi",
        "-itsoffset",
        f"{offset_s:.6f}",
        "-i",
        f"sine=frequency=1000:sample_rate=48000:duration={duration_s}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "pcm_s16le",
        "-shortest",
        "-y",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0 and path.exists() and path.stat().st_size > 0,
        "path": str(path),
        "bytes": path.stat().st_size if path.exists() else 0,
        "injected_offset_s": offset_s,
        "injected_offset_ms": round(offset_s * 1000.0, 3),
        "duration_s": duration_s,
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def apply_drift_correction(
    *,
    ffmpeg: str,
    source: Path,
    dest: Path,
    correction_ms: float,
) -> dict:
    """Shift audio by correction_ms (negative pulls late audio earlier)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    # ffmpeg -itsoffset delays the following input. To advance late audio we
    # delay video by the same amount when remuxing video from source and audio
    # from a delayed copy is awkward; instead delay the opposite stream:
    # correction_ms > 0 means audio is late -> delay video by correction.
    # correction_ms < 0 means audio is early -> delay audio by abs(correction).
    correction_s = correction_ms / 1000.0
    if abs(correction_s) < 1e-9:
        shutil.copyfile(source, dest)
        return {
            "ok": True,
            "path": str(dest),
            "correction_ms": 0.0,
            "method": "copy_no_shift",
        }
    if correction_s > 0:
        # Audio late: delay video so A/V align (equivalent to advancing audio).
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-itsoffset",
            f"{correction_s:.6f}",
            "-i",
            str(source),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            "-shortest",
            "-y",
            str(dest),
        ]
        method = "delay_video_to_match_late_audio"
    else:
        # Audio early: delay audio.
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-itsoffset",
            f"{abs(correction_s):.6f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "pcm_s16le",
            "-shortest",
            "-y",
            str(dest),
        ]
        method = "delay_early_audio"
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0,
        "path": str(dest),
        "correction_ms": round(correction_ms, 3),
        "method": method,
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def run_session(args: argparse.Namespace) -> dict:
    assert_media_medium(args.medium)
    ffmpeg = _ffmpeg()
    if not _ffprobe():
        raise SystemExit("ffprobe not found on PATH")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    samples: list[dict] = []
    base_offset_ms = args.offset_ms
    drift_per_sample_ms = args.drift_per_sample_ms
    sample_count = args.samples

    for index in range(sample_count):
        injected_ms = base_offset_ms + (index * drift_per_sample_ms)
        injected_s = injected_ms / 1000.0
        raw_path = OUT_DIR / f"raw_{index:02d}.mkv"
        corrected_path = OUT_DIR / f"corrected_{index:02d}.mkv"
        written = write_offset_mux(
            ffmpeg=ffmpeg,
            path=raw_path,
            offset_s=injected_s,
            duration_s=args.duration,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
        measured = probe_av_offset_ms(raw_path)
        measured_ms = measured.get("offset_ms")
        correction_ms = float(measured_ms) if measured_ms is not None else 0.0
        correction = apply_drift_correction(
            ffmpeg=ffmpeg,
            source=raw_path,
            dest=corrected_path,
            correction_ms=correction_ms,
        )
        residual = probe_av_offset_ms(corrected_path)
        sample = {
            "index": index,
            "injected_offset_ms": round(injected_ms, 3),
            "write": written,
            "measured": measured,
            "correction": correction,
            "residual": residual,
            "wall_s": round(time.perf_counter() - t0, 3),
        }
        samples.append(sample)

    residuals = [
        abs(float(s["residual"]["offset_ms"]))
        for s in samples
        if s.get("residual", {}).get("ok") and s["residual"].get("offset_ms") is not None
    ]
    measured_offsets = [
        float(s["measured"]["offset_ms"])
        for s in samples
        if s.get("measured", {}).get("ok") and s["measured"].get("offset_ms") is not None
    ]
    max_residual_ms = max(residuals) if residuals else None
    max_abs_measured_ms = (
        max(abs(v) for v in measured_offsets) if measured_offsets else None
    )

    # Self-check: each sample measured close to injected; residual near zero.
    measure_tol_ms = args.measure_tolerance_ms
    residual_tol_ms = args.residual_tolerance_ms
    sample_ok = True
    for sample in samples:
        injected = sample["injected_offset_ms"]
        measured_ms = sample.get("measured", {}).get("offset_ms")
        residual_ms = sample.get("residual", {}).get("offset_ms")
        if measured_ms is None or residual_ms is None:
            sample_ok = False
            break
        if abs(float(measured_ms) - float(injected)) > measure_tol_ms:
            sample_ok = False
            break
        if abs(float(residual_ms)) > residual_tol_ms:
            sample_ok = False
            break
        if not sample.get("write", {}).get("ok"):
            sample_ok = False
            break
        if not sample.get("correction", {}).get("ok"):
            sample_ok = False
            break

    ok = sample_ok and len(samples) == sample_count
    elapsed = time.perf_counter() - t0
    result = {
        "task": "OM-016",
        "gate": "G5",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "device": {
            "hostname": platform.node(),
            "os": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "network": {
            "medium": args.medium,
            "note": "local lavfi mux + ffprobe start_time; not dual-device glass-to-glass",
        },
        "method": {
            "inject": "ffmpeg -itsoffset on lavfi sine vs color",
            "measure": "ffprobe stream start_time audio-video delta",
            "correct": "remux with inverse itsoffset (delay late/early stream)",
            "citations": [
                "https://ffmpeg.org/ffmpeg-all.html#Main-options",
                "https://ffmpeg.org/ffmpeg-filters.html#adelay",
                "https://ffmpeg.org/ffprobe.html",
            ],
        },
        "config": {
            "base_offset_ms": base_offset_ms,
            "drift_per_sample_ms": drift_per_sample_ms,
            "samples": sample_count,
            "duration_s": args.duration,
            "measure_tolerance_ms": measure_tol_ms,
            "residual_tolerance_ms": residual_tol_ms,
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
        },
        "samples": samples,
        "summary": {
            "max_abs_measured_ms": max_abs_measured_ms,
            "max_residual_ms": max_residual_ms,
            "sample_count": len(samples),
            "rehearsal_wall_s": round(elapsed, 3),
        },
        "wall_elapsed_s": round(elapsed, 3),
        "ok": ok,
        "self_check": args.self_check,
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "selfcheck" if args.self_check else "measure"
    out_path = RESULTS_DIR / f"g5_av_sync_{mode}_{stamp}.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["result_path"] = str(out_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="OM-016 A/V sync G5 harness")
    parser.add_argument("--medium", default="wifi", choices=["wifi", "ethernet"])
    parser.add_argument("--offset-ms", type=float, default=80.0)
    parser.add_argument("--drift-per-sample-ms", type=float, default=5.0)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--duration", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument(
        "--measure-tolerance-ms",
        type=float,
        default=2.0,
        help="Allowed |measured-injected| for harness integrity (not a product target)",
    )
    parser.add_argument(
        "--residual-tolerance-ms",
        type=float,
        default=2.0,
        help="Allowed |residual| after correction for harness integrity",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="CI mode: short lavfi samples; print AV_SYNC_OK",
    )
    args = parser.parse_args()
    if args.self_check:
        args.samples = min(args.samples, 3)
        args.duration = min(args.duration, 2.0)
    result = run_session(args)
    if args.self_check:
        if not result["ok"]:
            print("AV_SYNC_FAIL", json.dumps(result, indent=2))
            return 1
        summary = result["summary"]
        print(
            "AV_SYNC_OK",
            f"samples={summary['sample_count']}",
            f"max_abs_measured_ms={summary['max_abs_measured_ms']}",
            f"max_residual_ms={summary['max_residual_ms']}",
            f"rehearsal_wall_s={summary['rehearsal_wall_s']}",
        )
        return 0
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
