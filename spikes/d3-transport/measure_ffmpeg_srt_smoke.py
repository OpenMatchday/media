#!/usr/bin/env python3
"""OM-004 spike: Haivision libsrt smoke via ffmpeg (build flag --enable-libsrt).

Starts an SRT listener and caller exchanging a short MPEG-TS test pattern.
Records wall-clock duration and ffmpeg exit codes. This is not packet-level
one-way latency; use srt_probe for that. Confirms the host ffmpeg can speak SRT.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9300)
    parser.add_argument("--seconds", type=float, default=3.0)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    listen_url = f"srt://{args.host}:{args.port}?mode=listener&latency=120000"
    call_url = f"srt://{args.host}:{args.port}?mode=caller&latency=120000"

    listener = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-c:a",
            "aac",
            "-t",
            str(args.seconds),
            "-f",
            "mpegts",
            listen_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.8)
    t0 = time.perf_counter()
    caller = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            call_url,
            "-t",
            str(args.seconds),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    try:
        listener.wait(timeout=args.seconds + 5)
    except subprocess.TimeoutExpired:
        listener.kill()
        listener.wait(timeout=5)

    listener_err = ""
    if listener.stderr:
        listener_err = listener.stderr.read().decode("utf-8", errors="replace")[-2000:]

    report = {
        "probe": "ffmpeg-srt-smoke",
        "ffmpeg_srt_enabled": True,
        "listen_url": listen_url,
        "call_url": call_url,
        "configured_seconds": args.seconds,
        "caller_elapsed_s": elapsed,
        "listener_returncode": listener.returncode,
        "caller_returncode": caller.returncode,
        "caller_stderr_tail": (caller.stderr or "")[-2000:],
        "listener_stderr_tail": listener_err,
        "metric": "SRT session smoke via ffmpeg linked with libsrt; not packet latency",
        "host_time_unix": time.time(),
    }
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="ascii")

    if caller.returncode != 0 or listener.returncode not in (0, None):
        print("FFMPEG_SRT_SMOKE_FAIL", file=sys.stderr)
        return 1
    print("FFMPEG_SRT_SMOKE_PASS", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
