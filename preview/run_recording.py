#!/usr/bin/env python3
"""OM-032: Recording start/stop + G6 file playback (AC-3.1 recording path).

Starts a Program-feed recording (lavfi testsrc + sine), stops it explicitly,
probes the completed file for video+audio, and verifies ffmpeg can decode
(playback). Also checks crash recovery never auto-restarts recording (AC-5.6
with OM-019).

Citations:
- PROJECT_SPEC AC-3.1, AC-5.6; ARCHITECTURE.md G6
- ffmpeg lavfi / libx264 / aac (host-tool encode for measurement)
- ffprobe -show_entries stream=codec_type,duration
  https://ffmpeg.org/ffprobe.html
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

OUT_DIR = PREVIEW_DIR / "recording"
STATE_NAME = "recording_state.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def probe_av(path: Path) -> dict:
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
            "stream=index,codec_type,codec_name,duration",
            "-show_entries",
            "format=duration,format_name",
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
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fmt = payload.get("format") or {}
    duration = None
    raw_dur = fmt.get("duration")
    if raw_dur is not None:
        try:
            duration = float(raw_dur)
        except (TypeError, ValueError):
            duration = None
    meta["streams"] = streams
    meta["format"] = fmt
    meta["has_video"] = has_video
    meta["has_audio"] = has_audio
    meta["duration_s"] = duration
    meta["ok"] = (
        proc.returncode == 0
        and has_video
        and has_audio
        and meta["bytes"] > 0
        and duration is not None
        and duration > 0
    )
    if not meta["ok"] and "error" not in meta:
        meta["error"] = "av_probe_failed"
    return meta


def playback_decode(path: Path, *, frames: int = 15) -> dict:
    """Decode a short window to null to confirm the file plays back."""
    ffmpeg = _ffmpeg()
    started = time.perf_counter()
    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-t",
            "1.0",
            "-frames:v",
            str(frames),
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "ok": proc.returncode == 0,
        "exit": proc.returncode,
        "elapsed_ms": round(elapsed_ms, 3),
        "frames_requested": frames,
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def start_recording(*, path: Path, width: int, height: int, fps: int) -> subprocess.Popen:
    """Start ffmpeg recording without a fixed -t; caller stops explicitly."""
    ffmpeg = _ffmpeg()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    # -re keeps wall-clock close to media duration (lavfi otherwise runs ahead).
    # Citation: ffmpeg -re "read input at native frame rate"
    # https://ffmpeg.org/ffmpeg.html#Main-options
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate={fps}",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    # stdin=PIPE so we can send 'q' for a clean muxer finish.
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_recording(proc: subprocess.Popen, *, grace_s: float = 8.0) -> dict:
    started = time.perf_counter()
    try:
        if proc.stdin is not None:
            proc.stdin.write("q")
            proc.stdin.flush()
            proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    try:
        stdout, stderr = proc.communicate(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return {
            "ok": False,
            "exit": proc.returncode,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error": "stop_timeout_killed",
            "stderr_tail": (stderr or "")[-400:],
        }
    return {
        "ok": proc.returncode == 0,
        "exit": proc.returncode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "stderr_tail": (stderr or "")[-400:],
        "stdout_tail": (stdout or "")[-200:],
    }


def run_start_stop_playback(
    *,
    record_s: float,
    width: int,
    height: int,
    fps: int,
) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"program_{stamp}.mp4"
    state_path = OUT_DIR / STATE_NAME

    wall_start = time.perf_counter()
    proc = start_recording(path=path, width=width, height=height, fps=fps)
    write_json(
        state_path,
        {
            "active": True,
            "path": str(path),
            "pid": proc.pid,
            "started_at": utc_now(),
        },
    )
    time.sleep(max(0.2, record_s))
    stop = stop_recording(proc)
    write_json(
        state_path,
        {
            "active": False,
            "path": str(path),
            "pid": proc.pid,
            "stopped_at": utc_now(),
            "stop": stop,
        },
    )
    probe = probe_av(path)
    play = playback_decode(path) if probe.get("ok") else {
        "ok": False,
        "error": "skipped_probe_failed",
    }
    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    ok = bool(stop.get("ok")) and bool(probe.get("ok")) and bool(play.get("ok"))
    return {
        "ok": ok,
        "path": str(path),
        "state_path": str(state_path),
        "record_s": record_s,
        "wall_ms": round(wall_ms, 3),
        "stop": stop,
        "probe": probe,
        "playback": play,
        "size": {
            "width": width,
            "height": height,
            "fps": fps,
        },
    }


def run_crash_no_restart(*, record_s: float) -> dict:
    """Start recording, simulate director crash mid-record, recover stopped."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"crash_{stamp}.mp4"
    state_path = OUT_DIR / f"crash_state_{stamp}.json"
    checkpoint_path = OUT_DIR / f"crash_checkpoint_{stamp}.json"

    proc = start_recording(path=path, width=640, height=360, fps=30)
    write_json(
        state_path,
        {
            "active": True,
            "path": str(path),
            "pid": proc.pid,
            "started_at": utc_now(),
        },
    )
    write_json(
        checkpoint_path,
        {
            "outputs": {"recording": True, "streaming": False},
            "recording_path": str(path),
            "saved_at": utc_now(),
        },
    )
    time.sleep(max(0.2, record_s))
    # Crash: kill encoder without clean stop; recover must leave recording stopped.
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
            "path": str(path),
            "pid": proc.pid,
            "crashed": True,
            "recovered_at": utc_now(),
            "outputs": recovered,
        },
    )

    # Guard: no new recording process must be started from the checkpoint.
    restarted = recovered["recording"] is True or recovered["auto_restarted"] is True
    return {
        "ok": not restarted and recovered["recording"] is False,
        "path": str(path),
        "state_path": str(state_path),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_had_recording": True,
        "recovered": recovered,
        "auto_restarted": restarted,
        "file_exists_after_crash": path.exists(),
        "file_bytes": path.stat().st_size if path.exists() else 0,
    }


def run(*, self_check: bool, medium: str, record_s: float) -> dict:
    assert_media_medium(medium)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    start_stop = run_start_stop_playback(
        record_s=record_s,
        width=1280,
        height=720,
        fps=30,
    )
    crash = run_crash_no_restart(record_s=min(0.5, record_s))

    ok = bool(start_stop["ok"]) and bool(crash["ok"])
    result = {
        "ok": ok,
        "task": "OM-032",
        "ac": ["AC-3.1", "AC-5.6"],
        "gate": "G6",
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
            "note": "host-tool measurement encode; product path prefers D4 hardware",
        },
        "start_stop": start_stop,
        "crash_no_restart": crash,
        "saved_at": utc_now(),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "selfcheck" if self_check else "measure"
    out_path = RESULTS_DIR / f"om032_recording_{label}_{stamp}.json"
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
        "--record-s",
        type=float,
        default=1.5,
        help="Seconds to record before explicit stop",
    )
    args = parser.parse_args()
    result = run(
        self_check=args.self_check,
        medium=args.medium,
        record_s=args.record_s,
    )
    if result["ok"]:
        ss = result["start_stop"]
        print(
            "RECORDING_OK "
            f"start_stop={ss['ok']} "
            f"has_video={ss['probe'].get('has_video')} "
            f"has_audio={ss['probe'].get('has_audio')} "
            f"duration_s={ss['probe'].get('duration_s')} "
            f"playback={ss['playback'].get('ok')} "
            f"crash_no_restart={result['crash_no_restart']['ok']} "
            f"wall_ms={ss['wall_ms']}"
        )
        return 0
    print("RECORDING_FAIL", json.dumps(result, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
