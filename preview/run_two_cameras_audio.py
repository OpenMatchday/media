#!/usr/bin/env python3
"""OM-014: two camera Preview paths + independent audio source with meters.

AC-2.4: audio source is selectable and not forced to follow the video source.
AC-2.5: peak/RMS meters are recorded for the selected audio path.
AC-1.3: Wi-Fi/Ethernet only.

Self-check uses two lavfi testsrc video paths + lavfi sine audio (no hardware).
Camera mode uses available dshow video devices; if only one physical device is
present, cam-2 falls back to lavfi testsrc and that fact is recorded.

Citations:
- DirectShow (Windows): https://ffmpeg.org/ffmpeg-devices.html#dshow
- lavfi sine / testsrc / astats: https://ffmpeg.org/ffmpeg-filters.html
- SRT (D3): Haivision libsrt
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "preview"))
sys.path.insert(0, str(ROOT / "gates"))
from media_medium import assert_media_medium  # noqa: E402
from run_one_camera import (  # noqa: E402
    PREVIEW_DIR,
    RESULTS_DIR,
    _ffmpeg,
    _ffprobe,
    encoder_args,
    list_dshow_video_devices,
    pick_encoder,
)


def list_dshow_audio_devices(ffmpeg: str) -> list[str]:
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    names: list[str] = []
    for line in text.splitlines():
        if "(audio)" not in line:
            continue
        start = line.find('"')
        end = line.find('"', start + 1) if start >= 0 else -1
        if start >= 0 and end > start:
            name = line[start + 1 : end]
            if name and name not in names:
                names.append(name)
    return names


def parse_astats_levels(stderr_text: str) -> dict:
    peaks = [float(v) for v in re.findall(r"Peak level dB:\s*([-0-9.]+)", stderr_text)]
    rms = [float(v) for v in re.findall(r"RMS level dB:\s*([-0-9.]+)", stderr_text)]
    meta_peaks = [
        float(v)
        for v in re.findall(
            r"lavfi\.astats\.Overall\.Peak_level=([-0-9.]+)", stderr_text
        )
    ]
    if not peaks and meta_peaks:
        peaks = meta_peaks
    if not peaks:
        return {"ok": False, "peak_db": None, "rms_db": None, "samples": 0}
    return {
        "ok": True,
        "peak_db": max(peaks),
        "rms_db": max(rms) if rms else None,
        "samples": len(peaks),
        "peak_db_min": min(peaks),
        "peak_db_last": peaks[-1],
    }


def build_video_input(
    *,
    source: str,
    device: str | None,
    width: int,
    height: int,
    fps: int,
    label: str,
) -> tuple[list[str], dict]:
    meta: dict = {"source_mode": source, "label": label}
    if source == "testsrc":
        return (
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc=size={width}x{height}:rate={fps}",
            ],
            meta,
        )
    if source == "camera":
        if platform.system() != "Windows":
            raise SystemExit("camera source currently wired for Windows dshow only")
        if not device:
            raise SystemExit(f"no dshow video device for {label}")
        meta["device"] = device
        return (["-f", "dshow", "-i", f"video={device}"], meta)
    raise SystemExit(f"unknown source {source}")


def run_one_preview_path(
    *,
    ffmpeg: str,
    encoder: str,
    input_args: list[str],
    host: str,
    port: int,
    srt_latency_us: int,
    seconds: float,
    snapshot: Path,
) -> dict:
    if snapshot.exists():
        snapshot.unlink()
    listen_url = f"srt://{host}:{port}?mode=listener&latency={srt_latency_us}"
    call_url = f"srt://{host}:{port}?mode=caller&latency={srt_latency_us}"
    sender_cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        *input_args,
        *encoder_args(encoder),
        "-an",
        "-t",
        str(seconds),
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
    t0 = time.perf_counter()
    sender = subprocess.Popen(
        sender_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.9)
    receiver = subprocess.run(receiver_cmd, capture_output=True, text=True)
    try:
        sender.wait(timeout=seconds + 8)
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
    return {
        "ok": ok,
        "listen_url": listen_url,
        "call_url": call_url,
        "snapshot_path": str(snapshot.relative_to(ROOT)).replace("\\", "/"),
        "snapshot_bytes": snapshot.stat().st_size if snapshot.exists() else 0,
        "ffprobe": frame_probe,
        "wall_elapsed_s": elapsed,
        "sender_returncode": sender.returncode,
        "receiver_returncode": receiver.returncode,
        "sender_stderr_tail": sender_err,
        "receiver_stderr_tail": (receiver.stderr or "")[-2000:],
    }


def measure_audio(
    *,
    ffmpeg: str,
    audio_source: str,
    audio_device: str | None,
    seconds: float,
) -> dict:
    meta: dict = {"source_mode": audio_source, "follows_video": False}
    if audio_source == "sine":
        input_args = [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
        ]
        meta["generator"] = "sine=1000Hz"
    elif audio_source == "device":
        if platform.system() != "Windows":
            raise SystemExit("device audio currently wired for Windows dshow only")
        if not audio_device:
            raise SystemExit("no dshow audio device selected")
        input_args = ["-f", "dshow", "-i", f"audio={audio_device}"]
        meta["device"] = audio_device
    else:
        raise SystemExit(f"unknown audio source {audio_source}")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        *input_args,
        "-t",
        str(seconds),
        "-af",
        "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.Peak_level",
        "-f",
        "null",
        "-",
    ]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.perf_counter() - t0
    levels = parse_astats_levels((proc.stderr or "") + (proc.stdout or ""))
    return {
        **meta,
        "ok": proc.returncode == 0 and levels.get("ok") is True,
        "returncode": proc.returncode,
        "wall_elapsed_s": elapsed,
        "meters": levels,
        "stderr_tail": (proc.stderr or "")[-2000:],
    }


def resolve_camera_pair(args: argparse.Namespace, ffmpeg: str) -> tuple[dict, dict]:
    """Return cam-1 and cam-2 input specs."""
    if args.source == "testsrc":
        return (
            {
                "source": "testsrc",
                "device": None,
                "id": "cam-1",
                "note": "lavfi testsrc",
            },
            {
                "source": "testsrc",
                "device": None,
                "id": "cam-2",
                "note": "lavfi testsrc (second path)",
            },
        )

    devices = list_dshow_video_devices(ffmpeg)
    # Prefer real (video) devices; skip known non-binding virtual names when others exist.
    usable = [d for d in devices if d.lower() != "lsvcam"]
    if not usable:
        usable = list(devices)

    cam1_device = args.device1 or (usable[0] if usable else None)
    cam2_device = args.device2 or (usable[1] if len(usable) > 1 else None)

    cam1 = {
        "source": "camera",
        "device": cam1_device,
        "id": "cam-1",
        "note": "dshow video",
        "inventory": devices,
    }
    if cam2_device:
        cam2 = {
            "source": "camera",
            "device": cam2_device,
            "id": "cam-2",
            "note": "dshow video",
            "inventory": devices,
        }
    else:
        # shortcut: only one physical dshow video device on this host; second path uses lavfi until a second camera is attached
        cam2 = {
            "source": "testsrc",
            "device": None,
            "id": "cam-2",
            "note": "fallback lavfi testsrc; only one usable dshow video device",
            "inventory": devices,
        }
    return cam1, cam2


def run_session(args: argparse.Namespace) -> dict:
    assert_media_medium(args.medium)
    ffmpeg = _ffmpeg()
    encoder = pick_encoder(ffmpeg, args.encoder)
    cam1_spec, cam2_spec = resolve_camera_pair(args, ffmpeg)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    audio_devices = (
        list_dshow_audio_devices(ffmpeg) if platform.system() == "Windows" else []
    )
    if args.audio_source == "device":
        audio_device = args.audio_device or (audio_devices[0] if audio_devices else None)
    else:
        audio_device = None

    # Selected audio must remain independent of whichever camera is on Preview/Program.
    selected_audio = {
        "id": args.audio_id,
        "source": args.audio_source,
        "device": audio_device,
        "follows_video_program": False,
        "follows_preview_source": False,
        "available_devices": audio_devices,
    }

    started = datetime.now(timezone.utc).isoformat()
    paths = []
    for spec, port, snap_name in (
        (cam1_spec, args.port, "director_preview_cam1.jpg"),
        (cam2_spec, args.port + 1, "director_preview_cam2.jpg"),
    ):
        input_args, input_meta = build_video_input(
            source=spec["source"],
            device=spec.get("device"),
            width=args.width,
            height=args.height,
            fps=args.fps,
            label=spec["id"],
        )
        snap = PREVIEW_DIR / snap_name
        result = run_one_preview_path(
            ffmpeg=ffmpeg,
            encoder=encoder,
            input_args=input_args,
            host=args.host,
            port=port,
            srt_latency_us=args.srt_latency_us,
            seconds=args.seconds,
            snapshot=snap,
        )
        paths.append(
            {
                "camera_id": spec["id"],
                "spec": {**spec, **input_meta},
                "result": result,
            }
        )

    audio = measure_audio(
        ffmpeg=ffmpeg,
        audio_source=args.audio_source,
        audio_device=audio_device,
        seconds=min(args.seconds, 2.0),
    )

    cameras_ok = all(p["result"]["ok"] for p in paths)
    ok = cameras_ok and audio["ok"]

    report = {
        "task": "OM-014",
        "acs": ["AC-2.4", "AC-2.5"],
        "started_utc": started,
        "device": {
            "hostname": platform.node(),
            "os": platform.platform(),
            "python": platform.python_version(),
        },
        "network": {
            "medium": args.medium,
            "bind_host": args.host,
            "note": "loopback or NIC hairpin; dual camera paths on one host",
        },
        "codec": {
            "encoder": encoder,
            "hardware_preferred": encoder in {"h264_amf", "h264_mf"},
        },
        "cameras": paths,
        "audio": {
            "selected": selected_audio,
            "measurement": audio,
            "meters_visible_fields": ["peak_db", "rms_db"],
        },
        "ok": ok,
    }
    return report


def self_check(args: argparse.Namespace) -> int:
    args.source = "testsrc"
    args.audio_source = "sine"
    args.seconds = min(args.seconds, 3.0)
    args.encoder = args.encoder or "libx264"
    report = run_session(args)
    out = RESULTS_DIR / "om014-self-check.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["ok"]:
        print("TWO_CAMERAS_AUDIO_FAIL", file=sys.stderr)
        print(json.dumps(report, indent=2))
        return 1
    cam_bytes = [c["result"]["snapshot_bytes"] for c in report["cameras"]]
    meters = report["audio"]["measurement"]["meters"]
    print(
        "TWO_CAMERAS_AUDIO_OK "
        f"encoder={report['codec']['encoder']} "
        f"cam_bytes={cam_bytes} "
        f"audio_peak_db={meters.get('peak_db')} "
        f"audio_rms_db={meters.get('rms_db')}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="two testsrc + sine audio; writes om014-self-check.json",
    )
    parser.add_argument("--source", choices=("camera", "testsrc"), default="camera")
    parser.add_argument("--device1", default=None, help="dshow video name for cam-1")
    parser.add_argument("--device2", default=None, help="dshow video name for cam-2")
    parser.add_argument(
        "--audio-source",
        choices=("device", "sine"),
        default="device",
        help="independent audio path (not tied to video Program)",
    )
    parser.add_argument("--audio-device", default=None, help="dshow audio device name")
    parser.add_argument("--audio-id", default="aud-1", help="logical audio source id")
    parser.add_argument("--medium", default="wifi", help="wifi|ethernet only")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9410)
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
    out = args.out or (RESULTS_DIR / f"om014-{args.source}-{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = {
        "ok": report["ok"],
        "out": str(out),
        "codec": report["codec"],
        "cameras": [
            {
                "id": c["camera_id"],
                "ok": c["result"]["ok"],
                "bytes": c["result"]["snapshot_bytes"],
                "note": c["spec"].get("note"),
            }
            for c in report["cameras"]
        ],
        "audio": {
            "selected": report["audio"]["selected"]["id"],
            "follows_video": report["audio"]["selected"]["follows_video_program"],
            "meters": report["audio"]["measurement"]["meters"],
        },
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    raise SystemExit(main())
