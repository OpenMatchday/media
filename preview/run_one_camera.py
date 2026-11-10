#!/usr/bin/env python3
"""OM-013: one camera capture -> encode -> SRT -> director receive (Preview path).

Wi-Fi/Ethernet only (AC-1.3). Starts G1/G2 measurement records; does not claim
glass-to-glass product targets. Prefer hardware encode (D4); fall back only when
needed. Self-check uses lavfi testsrc (no camera required for CI).

Citations:
- DirectShow capture (Windows): ffmpeg dshow
  https://ffmpeg.org/ffmpeg-devices.html#dshow
- SRT (D3): Haivision libsrt / draft-sharabayko-srt-01
- Encoders (D4): h264_amf, h264_mf, OpenH264/libx264 host-tool notes in spikes/d4-codecs
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gates"))
from media_medium import assert_media_medium  # noqa: E402

RESULTS_DIR = ROOT / "measurements" / "results"
PREVIEW_DIR = ROOT / "preview" / "out"


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg not found on PATH")
    return path


def _ffprobe() -> str | None:
    return shutil.which("ffprobe")


def list_dshow_video_devices(ffmpeg: str) -> list[str]:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    names: list[str] = []
    for line in text.splitlines():
        if '(video)' not in line and '(none)' not in line:
            continue
        # e.g. [in#0 @ ...] "ASUS HD webcam" (video)
        start = line.find('"')
        end = line.find('"', start + 1) if start >= 0 else -1
        if start >= 0 and end > start:
            name = line[start + 1 : end]
            if name and name not in names:
                names.append(name)
    return names


def pick_encoder(ffmpeg: str, prefer: str | None) -> str:
    candidates = []
    if prefer:
        candidates.append(prefer)
    # D4 order on Windows: AMF, Media Foundation, then host-tool libx264 for CI/self-check
    candidates.extend(["h264_amf", "h264_mf", "libx264"])
    seen: set[str] = set()
    ordered = [c for c in candidates if not (c in seen or seen.add(c))]
    enc = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    available = enc.stdout or ""
    for name in ordered:
        if name in available:
            return name
    raise SystemExit("no usable H.264 encoder found")


def encoder_args(name: str) -> list[str]:
    if name == "libx264":
        return [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
        ]
    if name == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-usage", "ultralowlatency"]
    if name == "h264_mf":
        return ["-c:v", "h264_mf", "-hw_encoding", "true"]
    return ["-c:v", name]


def build_input(args: argparse.Namespace, ffmpeg: str) -> tuple[list[str], dict]:
    meta: dict = {"source_mode": args.source}
    if args.source == "testsrc":
        return (
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size={args.width}x{args.height}:rate={args.fps}",
            ],
            meta,
        )
    if args.source == "camera":
        if platform.system() != "Windows":
            raise SystemExit("camera source currently wired for Windows dshow only")
        devices = list_dshow_video_devices(ffmpeg)
        meta["dshow_devices"] = devices
        device = args.device or (devices[0] if devices else None)
        if not device:
            raise SystemExit("no dshow video device found")
        meta["device"] = device
        return (["-f", "dshow", "-i", f"video={device}"], meta)
    raise SystemExit(f"unknown source {args.source}")


def run_session(args: argparse.Namespace) -> dict:
    assert_media_medium(args.medium)
    ffmpeg = _ffmpeg()
    encoder = pick_encoder(ffmpeg, args.encoder)
    input_args, input_meta = build_input(args, ffmpeg)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = PREVIEW_DIR / "director_preview.jpg"
    if snapshot.exists():
        snapshot.unlink()

    listen_url = (
        f"srt://{args.host}:{args.port}?mode=listener&latency={args.srt_latency_us}"
    )
    call_url = (
        f"srt://{args.host}:{args.port}?mode=caller&latency={args.srt_latency_us}"
    )

    sender_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        *input_args,
        *encoder_args(encoder),
        "-an",
        "-t",
        str(args.seconds),
        "-f",
        "mpegts",
        listen_url,
    ]
    receiver_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        call_url,
        "-frames:v",
        "1",
        "-update",
        "1",
        "-y",
        str(snapshot),
    ]

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    sender = subprocess.Popen(
        sender_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.9)
    receiver = subprocess.run(receiver_cmd, capture_output=True, text=True)
    try:
        sender.wait(timeout=args.seconds + 8)
    except subprocess.TimeoutExpired:
        sender.kill()
        sender.wait(timeout=5)
    elapsed = time.perf_counter() - t0

    sender_err = ""
    if sender.stderr:
        sender_err = sender.stderr.read().decode("utf-8", errors="replace")[-2000:]

    frame_probe: dict = {}
    probe = _ffprobe()
    if probe and snapshot.exists() and snapshot.stat().st_size > 0:
        probed = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name",
                "-of",
                "json",
                str(snapshot),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            frame_probe = json.loads(probed.stdout or "{}")
        except json.JSONDecodeError:
            frame_probe = {"parse_error": True}

    ok = (
        sender.returncode == 0
        and receiver.returncode == 0
        and snapshot.exists()
        and snapshot.stat().st_size > 0
    )

    report = {
        "task": "OM-013",
        "gate_notes": {
            "G1": "capture + encode path exercised; hardware preferred when available",
            "G2": "SRT transport to director receive started; single-host path only",
        },
        "started_utc": started,
        "device": {
            "hostname": platform.node(),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "network": {
            "medium": args.medium,
            "bind_host": args.host,
            "note": "loopback or NIC hairpin; not dual-device consumer-router glass-to-glass",
        },
        "transport": {
            "name": "SRT",
            "listen_url": listen_url,
            "call_url": call_url,
            "configured_latency_us": args.srt_latency_us,
        },
        "codec": {
            "encoder": encoder,
            "hardware_preferred": encoder in {"h264_amf", "h264_mf"},
        },
        "input": {
            **input_meta,
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "seconds": args.seconds,
        },
        "director_preview": {
            "snapshot_path": str(snapshot.relative_to(ROOT)).replace("\\", "/"),
            "snapshot_bytes": snapshot.stat().st_size if snapshot.exists() else 0,
            "ffprobe": frame_probe,
        },
        "timing": {
            "wall_elapsed_s": elapsed,
        },
        "sender_returncode": sender.returncode,
        "receiver_returncode": receiver.returncode,
        "sender_stderr_tail": sender_err,
        "receiver_stderr_tail": (receiver.stderr or "")[-2000:],
        "ok": ok,
    }
    return report


def self_check(args: argparse.Namespace) -> int:
    args.source = "testsrc"
    args.seconds = min(args.seconds, 3.0)
    args.encoder = args.encoder or "libx264"
    report = run_session(args)
    out = RESULTS_DIR / "om013-self-check.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["ok"]:
        print("ONE_CAMERA_PREVIEW_FAIL", file=sys.stderr)
        print(json.dumps(report, indent=2))
        return 1
    print(
        "ONE_CAMERA_PREVIEW_OK "
        f"encoder={report['codec']['encoder']} "
        f"bytes={report['director_preview']['snapshot_bytes']} "
        f"elapsed_s={report['timing']['wall_elapsed_s']:.3f}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="lavfi testsrc + SRT loopback; writes om013-self-check.json",
    )
    parser.add_argument("--source", choices=("camera", "testsrc"), default="camera")
    parser.add_argument("--device", default=None, help="dshow device name")
    parser.add_argument("--medium", default="wifi", help="wifi|ethernet only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    parser.add_argument("--srt-latency-us", type=int, default=120000)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--encoder", default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.self_check:
        return self_check(args)

    report = run_session(args)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or (RESULTS_DIR / f"om013-{args.source}-{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "out": str(out), **{k: report[k] for k in ("codec", "director_preview", "timing")}}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    # Avoid Windows console code-page issues for device names
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
