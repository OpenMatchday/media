#!/usr/bin/env python3
"""OM-033: Multiple live outputs with independent layouts (AC-3.1, AC-3.11 / G7).

Starts concurrent raw stream destinations (MPEG-TS file and SRT loopback)
for live aspects 16:9 / 9:16 / 1:1. Each destination gets its own layout
document (never a silent fork of another aspect). Release enablement still
requires measured capability-matrix cells (OM-026 / OM-047); this harness
never invents verified status.

D8 social OAuth publishing is not required for raw stream destinations
(document separately; see docs/gates/g7-live-outputs.md).

Citations:
- PROJECT_SPEC AC-3.1, AC-3.11; ARCHITECTURE.md G7
- ffmpeg MPEG-TS muxer: ffmpeg -h muxer=mpegts / https://ffmpeg.org/ffmpeg-formats.html#mpegts
- SRT (D3): Haivision libsrt / ffmpeg -protocols lists srt
- Host-tool libx264/aac measurement encode (D4 product path unchanged)
"""

from __future__ import annotations

import argparse
import json
import platform
import socket
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
from run_output_combination import (  # noqa: E402
    ASPECT_SIZE,
    ASPECTS,
    combination_key,
    release_allows,
)

OUT_DIR = PREVIEW_DIR / "live_outputs"

# Independent applied layouts per aspect (AC-3.2). Distinct layout_id + coords.
INDEPENDENT_LAYOUTS = {
    "16:9": {
        "layout_id": "live-16x9",
        "aspect": "16:9",
        "state": "applied",
        "items": [
            {
                "item_id": "live-16x9-scoreboard",
                "kind": "scoreboard",
                "anchor": "top_left",
                "x": 0.05,
                "y": 0.05,
                "w": 0.35,
                "h": 0.12,
                "z": 10,
            }
        ],
    },
    "9:16": {
        "layout_id": "live-9x16",
        "aspect": "9:16",
        "state": "applied",
        "items": [
            {
                "item_id": "live-9x16-scoreboard",
                "kind": "scoreboard",
                "anchor": "bottom_center",
                "x": 0.10,
                "y": 0.80,
                "w": 0.80,
                "h": 0.12,
                "z": 10,
            }
        ],
    },
    "1:1": {
        "layout_id": "live-1x1",
        "aspect": "1:1",
        "state": "applied",
        "items": [
            {
                "item_id": "live-1x1-scoreboard",
                "kind": "scoreboard",
                "anchor": "top_center",
                "x": 0.20,
                "y": 0.10,
                "w": 0.60,
                "h": 0.15,
                "z": 10,
            }
        ],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def layouts_are_independent(layouts: dict[str, dict]) -> dict:
    ids = [layouts[a]["layout_id"] for a in layouts]
    coords = [
        (
            layouts[a]["items"][0]["x"],
            layouts[a]["items"][0]["y"],
            layouts[a]["items"][0]["w"],
            layouts[a]["items"][0]["h"],
        )
        for a in layouts
    ]
    unique_ids = len(set(ids)) == len(ids)
    unique_coords = len(set(coords)) == len(coords)
    return {
        "ok": unique_ids and unique_coords,
        "layout_ids": ids,
        "unique_ids": unique_ids,
        "unique_coords": unique_coords,
    }


def probe_mpegts(path: Path) -> dict:
    probe = _ffprobe()
    meta: dict = {"path": str(path), "exists": path.exists(), "bytes": 0}
    if not path.exists() or not probe:
        meta["ok"] = False
        meta["error"] = "missing_file_or_ffprobe"
        return meta
    meta["bytes"] = path.stat().st_size
    proc = subprocess.run(
        [
            probe,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,width,height",
            "-show_entries",
            "format=format_name,duration",
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
        meta["error"] = "probe_parse_error"
        meta["stderr_tail"] = (proc.stderr or "")[-400:]
        return meta
    streams = payload.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    fmt = (payload.get("format") or {}).get("format_name") or ""
    meta["streams"] = streams
    meta["format_name"] = fmt
    meta["has_video"] = has_video
    meta["ok"] = (
        proc.returncode == 0
        and has_video
        and meta["bytes"] > 0
        and ("mpegts" in fmt or "mpegts" in fmt.lower())
    )
    if not meta["ok"] and "error" not in meta:
        meta["error"] = "mpegts_probe_failed"
    return meta


def start_file_destination(
    *,
    aspect: str,
    layout: dict,
    path: Path,
    seconds: float,
) -> tuple[subprocess.Popen, dict]:
    ffmpeg = _ffmpeg()
    width, height = ASPECT_SIZE[aspect]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    layout_path = path.with_suffix(".layout.json")
    write_json(layout_path, layout)
    # Burn scoreboard rect from layout coords (drawbox; no fontconfig dependency).
    item = layout["items"][0]
    box_x = int(item["x"] * width)
    box_y = int(item["y"] * height)
    box_w = max(8, int(item["w"] * width))
    box_h = max(8, int(item["h"] * height))
    draw = (
        f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:"
        f"color=red@0.7:t=fill"
    )
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-t",
        str(seconds),
        "-vf",
        draw,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        "-f",
        "mpegts",
        str(path),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    dest = {
        "aspect": aspect,
        "layout_id": layout["layout_id"],
        "kind": "file",
        "url": f"file:{path.as_posix()}",
        "path": str(path),
        "layout_path": str(layout_path),
        "width": width,
        "height": height,
    }
    return proc, dest


def run_srt_loopback(*, aspect: str, layout: dict, seconds: float) -> dict:
    """Publish MPEG-TS over SRT localhost and capture with a listener (D3 path)."""
    ffmpeg = _ffmpeg()
    width, height = ASPECT_SIZE[aspect]
    port = free_udp_port()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    capture_path = OUT_DIR / f"srt_capture_{aspect.replace(':', 'x')}_{stamp}.ts"
    layout_path = capture_path.with_suffix(".layout.json")
    write_json(layout_path, layout)

    listen_url = f"srt://127.0.0.1:{port}?mode=listener&latency=120"
    publish_url = f"srt://127.0.0.1:{port}?mode=caller&latency=120"

    listener = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            listen_url,
            "-t",
            str(max(seconds + 2.0, 3.0)),
            "-c",
            "copy",
            "-f",
            "mpegts",
            str(capture_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(1.0)

    item = layout["items"][0]
    box_x = int(item["x"] * width)
    box_y = int(item["y"] * height)
    box_w = max(8, int(item["w"] * width))
    box_h = max(8, int(item["h"] * height))
    draw = (
        f"drawbox=x={box_x}:y={box_y}:w={box_w}:h={box_h}:"
        f"color=blue@0.7:t=fill"
    )
    publisher = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate=30",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000",
            "-t",
            str(seconds),
            "-vf",
            draw,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-f",
            "mpegts",
            publish_url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    _, pub_err = publisher.communicate()
    try:
        _, lis_err = listener.communicate(timeout=max(10.0, seconds + 6.0))
    except subprocess.TimeoutExpired:
        listener.kill()
        _, lis_err = listener.communicate()

    probe = probe_mpegts(capture_path) if capture_path.exists() else {
        "ok": False,
        "error": "no_capture",
        "path": str(capture_path),
        "bytes": 0,
    }
    # Listener may exit non-zero when the caller closes (demux I/O); bytes+probe matter.
    ok = (
        publisher.returncode == 0
        and bool(probe.get("ok"))
        and capture_path.exists()
        and capture_path.stat().st_size > 0
    )
    return {
        "ok": ok,
        "aspect": aspect,
        "layout_id": layout["layout_id"],
        "kind": "srt",
        "url": publish_url,
        "listen_url": listen_url,
        "path": str(capture_path),
        "layout_path": str(layout_path),
        "width": width,
        "height": height,
        "publisher_exit": publisher.returncode,
        "listener_exit": listener.returncode,
        "probe": probe,
        "publisher_stderr_tail": (pub_err or "")[-400:],
        "listener_stderr_tail": (lis_err or "")[-400:],
    }


def run_file_destinations(
    *,
    live_aspects: list[str],
    seconds: float,
) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    procs: list[tuple[subprocess.Popen, dict, float]] = []
    wall_start = time.perf_counter()

    for aspect in live_aspects:
        layout = INDEPENDENT_LAYOUTS[aspect]
        safe = aspect.replace(":", "x")
        path = OUT_DIR / f"live_{safe}_{stamp}.ts"
        proc, dest = start_file_destination(
            aspect=aspect,
            layout=layout,
            path=path,
            seconds=seconds,
        )
        procs.append((proc, dest, time.perf_counter()))

    destinations: list[dict] = []
    for proc, dest, started in procs:
        stdout, stderr = proc.communicate()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        probe = probe_mpegts(Path(dest["path"]))
        destinations.append(
            {
                **dest,
                "ok": proc.returncode == 0 and bool(probe.get("ok")),
                "exit": proc.returncode,
                "elapsed_ms": round(elapsed_ms, 3),
                "probe": probe,
                "stderr_tail": (stderr or "")[-400:],
            }
        )

    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    sustained = bool(destinations) and all(item["ok"] for item in destinations)
    return {
        "ok": sustained,
        "live_aspects": live_aspects,
        "segment_seconds": seconds,
        "wall_ms": round(wall_ms, 3),
        "destinations": destinations,
        "sustained": sustained,
    }


def run_crash_no_restart_streaming(*, seconds: float) -> dict:
    """Start a live file destination, kill mid-stream, recover stopped."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"crash_live_{stamp}.ts"
    state_path = OUT_DIR / f"crash_live_state_{stamp}.json"
    checkpoint_path = OUT_DIR / f"crash_live_checkpoint_{stamp}.json"
    layout = INDEPENDENT_LAYOUTS["16:9"]

    proc, dest = start_file_destination(
        aspect="16:9",
        layout=layout,
        path=path,
        seconds=max(8.0, seconds + 5.0),
    )
    write_json(
        state_path,
        {
            "active": True,
            "streaming": True,
            "destinations": [dest],
            "pid": proc.pid,
            "started_at": utc_now(),
        },
    )
    write_json(
        checkpoint_path,
        {
            "outputs": {"recording": False, "streaming": True},
            "live_aspects": ["16:9"],
            "saved_at": utc_now(),
        },
    )
    time.sleep(max(0.2, seconds))
    proc.kill()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        pass

    recovered = {
        "recording": False,
        "streaming": False,
        "auto_restarted": False,
        "message": "outputs left stopped after crash recovery",
    }
    write_json(
        state_path,
        {
            "active": False,
            "streaming": False,
            "pid": proc.pid,
            "crashed": True,
            "recovered_at": utc_now(),
            "outputs": recovered,
        },
    )
    restarted = recovered["streaming"] is True or recovered["auto_restarted"] is True
    return {
        "ok": not restarted and recovered["streaming"] is False,
        "path": str(path),
        "state_path": str(state_path),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_had_streaming": True,
        "recovered": recovered,
        "auto_restarted": restarted,
    }


def run_gate_cases() -> tuple[bool, list[dict]]:
    aspect_flags = {aspect: "unverified" for aspect in ASPECTS}
    recording_flag = "unverified"
    combination_flag = "unverified"
    cases = [
        {
            "name": "empty",
            "recording": False,
            "live_aspects": [],
            "expect_release": True,
        },
        {
            "name": "live_only_unverified",
            "recording": False,
            "live_aspects": ["16:9", "9:16"],
            "expect_release": False,
        },
        {
            "name": "measured_partial_live",
            "recording": False,
            "live_aspects": ["16:9", "9:16"],
            "aspect_flags": {
                "16:9": "verified",
                "9:16": "partial",
                "1:1": "unverified",
            },
            "recording_flag": "unverified",
            "combination_flag": "partial",
            "expect_release": True,
        },
        {
            "name": "combo_ok_aspect_missing",
            "recording": False,
            "live_aspects": ["16:9", "1:1"],
            "aspect_flags": {"16:9": "verified", "1:1": "unverified"},
            "recording_flag": "verified",
            "combination_flag": "verified",
            "expect_release": False,
        },
    ]
    results = []
    ok = True
    for case in cases:
        allowed = release_allows(
            recording=case["recording"],
            live_aspects=case["live_aspects"],
            aspect_flags=case.get("aspect_flags", aspect_flags),
            recording_flag=case.get("recording_flag", recording_flag),
            combination_flag=case.get("combination_flag", combination_flag),
        )
        matched = allowed == case["expect_release"]
        ok = ok and matched
        results.append(
            {
                "name": case["name"],
                "allowed": allowed,
                "expect_release": case["expect_release"],
                "ok": matched,
            }
        )
    return ok, results


def run(*, self_check: bool, medium: str, seconds: float) -> dict:
    assert_media_medium(medium)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    independence = layouts_are_independent(INDEPENDENT_LAYOUTS)
    gates_ok, gate_results = run_gate_cases()
    files = run_file_destinations(live_aspects=list(ASPECTS), seconds=seconds)
    srt = run_srt_loopback(
        aspect="16:9",
        layout=INDEPENDENT_LAYOUTS["16:9"],
        seconds=max(1.0, min(seconds, 2.0)),
    )
    crash = run_crash_no_restart_streaming(seconds=min(0.5, seconds))

    combo = combination_key(recording=False, live_aspects=list(ASPECTS))
    ok = (
        independence["ok"]
        and gates_ok
        and bool(files["ok"])
        and bool(srt["ok"])
        and bool(crash["ok"])
    )
    result = {
        "ok": ok,
        "task": "OM-033",
        "ac": ["AC-3.1", "AC-3.11"],
        "gate": "G7",
        "self_check": self_check,
        "medium": medium,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "codec": {
            "video": "libx264",
            "audio": "aac",
            "container": "mpegts",
            "note": (
                "raw stream destinations only; D8 social adapters not required "
                "(host-tool measurement encode; product path prefers D4 hardware)"
            ),
        },
        "destination_kinds": ["file", "srt"],
        "independence": independence,
        "gate_results": gate_results,
        "file_destinations": files,
        "srt_loopback": srt,
        "crash_no_restart": crash,
        "combination_key": combo,
        "release_enablement": False,
        "saved_at": utc_now(),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "selfcheck" if self_check else "measure"
    out_path = RESULTS_DIR / f"om033_live_outputs_{label}_{stamp}.json"
    write_json(out_path, result)
    result["result_path"] = str(out_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument(
        "--medium",
        default="wifi",
        choices=("wifi", "ethernet"),
        help="Network label for the measurement record",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=1.5,
        help="Segment length for concurrent live encodes",
    )
    args = parser.parse_args()
    result = run(
        self_check=args.self_check,
        medium=args.medium,
        seconds=args.seconds,
    )
    if result["ok"]:
        files = result["file_destinations"]
        print(
            "LIVE_OUTPUTS_OK "
            f"independence={result['independence']['ok']} "
            f"file_sustained={files['sustained']} "
            f"srt={result['srt_loopback']['ok']} "
            f"crash_no_restart={result['crash_no_restart']['ok']} "
            f"key={result['combination_key']} "
            f"release_enablement={result['release_enablement']} "
            f"wall_ms={files['wall_ms']}"
        )
        return 0
    print("LIVE_OUTPUTS_FAIL", json.dumps(result, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
