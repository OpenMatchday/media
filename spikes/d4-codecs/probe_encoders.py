#!/usr/bin/env python3
"""OM-005 / D4 encode smoke probes via ffmpeg (host toolchain)."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
RESULTS = ROOT / "results"

ENCODERS = [
    ("h264_amf", ["-c:v", "h264_amf", "-b:v", "4M"]),
    ("hevc_amf", ["-c:v", "hevc_amf", "-b:v", "4M"]),
    ("h264_mf", ["-c:v", "h264_mf", "-b:v", "4M"]),
    ("libx264", ["-c:v", "libx264", "-preset", "veryfast", "-b:v", "4M"]),
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-b:v", "4M"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-b:v", "4M"]),
    ("h264_vaapi", ["-c:v", "h264_vaapi", "-b:v", "4M"]),
]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def ensure_source(frames: int, width: int, height: int, fps: int) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    src = OUT / f"src_{width}x{height}_{fps}_{frames}.yuv"
    if src.exists() and src.stat().st_size > 0:
        return src
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate={fps}",
        "-frames:v",
        str(frames),
        "-pix_fmt",
        "yuv420p",
        str(src),
    ]
    proc = run(cmd)
    if proc.returncode != 0 or not src.exists():
        raise SystemExit(f"source generation failed: {proc.stderr.strip()}")
    return src


def probe_one(
    name: str,
    enc_args: list[str],
    src: Path,
    frames: int,
    width: int,
    height: int,
    fps: int,
) -> dict:
    out_path = OUT / f"{name}.mp4"
    if out_path.exists():
        out_path.unlink()
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "yuv420p",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        str(src),
        *enc_args,
        "-frames:v",
        str(frames),
        str(out_path),
    ]
    t0 = time.perf_counter()
    proc = run(cmd)
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
    size = out_path.stat().st_size if out_path.exists() else 0
    ok = proc.returncode == 0 and size > 0
    stderr = (proc.stderr or "").strip()
    return {
        "encoder": name,
        "ok": ok,
        "exit_code": proc.returncode,
        "elapsed_ms": elapsed_ms,
        "output_bytes": size,
        "stderr_tail": stderr[-500:] if stderr else "",
        "command": cmd,
    }


def host_meta() -> dict:
    return {
        "device": platform.node(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cwd": os.getcwd(),
    }


def ffmpeg_meta() -> dict:
    proc = run(["ffmpeg", "-version"])
    first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    enc = run(["ffmpeg", "-hide_banner", "-encoders"])
    hw = run(["ffmpeg", "-hide_banner", "-hwaccels"])
    return {
        "version_line": first,
        "hwaccels": [ln.strip() for ln in (hw.stdout or "").splitlines() if ln.strip()],
        "encoder_names_present": sorted(
            name
            for name, _ in ENCODERS
            if name in (enc.stdout or "")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=90)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--out",
        type=Path,
        default=RESULTS / "encode-smokes.json",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="optional encoder name filter",
    )
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    src = ensure_source(args.frames, args.width, args.height, args.fps)
    selected = ENCODERS
    if args.only:
        wanted = set(args.only)
        selected = [e for e in ENCODERS if e[0] in wanted]

    probes = [
        probe_one(name, enc_args, src, args.frames, args.width, args.height, args.fps)
        for name, enc_args in selected
    ]
    payload = {
        "spike": "OM-005 / D4",
        "host": host_meta(),
        "ffmpeg": ffmpeg_meta(),
        "source": {
            "path": str(src),
            "bytes": src.stat().st_size,
            "frames": args.frames,
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "pix_fmt": "yuv420p",
        },
        "probes": probes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    for p in probes:
        print(f"{p['encoder']}: ok={p['ok']} exit={p['exit_code']} bytes={p['output_bytes']} ms={p['elapsed_ms']}")
    return 0 if any(p["ok"] for p in probes) else 1


if __name__ == "__main__":
    sys.exit(main())
