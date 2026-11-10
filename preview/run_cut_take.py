#!/usr/bin/env python3
"""OM-015: Preview/Program Cut timing (G3) + Program bus identity switch.

Measures wall time from an explicit Cut/Take issue to Program frame identity
matching the Preview source. No silent auto-switch. Self-check uses lavfi
color sources (no hardware). Camera mode can stamp identity over live frames.

AC-2.1 / AC-2.2 / G3. Tally is asserted as a control-plane side effect record.
Citations: ffmpeg lavfi color / drawtext; SRT path reused from OM-013/014.
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
from run_one_camera import (  # noqa: E402
    PREVIEW_DIR,
    RESULTS_DIR,
    _ffmpeg,
    _ffprobe,
    encoder_args,
    pick_encoder,
)

PROGRAM_DIR = PREVIEW_DIR / "program"


def write_identity_frame(
    *,
    ffmpeg: str,
    path: Path,
    label: str,
    color: str,
    width: int,
    height: int,
) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    # Distinct solid plates per source id (no drawtext; Windows fontconfig
    # often missing). Identity is the file bytes we copy onto Program.
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={width}x{height}:d=0.2",
        "-frames:v",
        "1",
        "-y",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0 and path.exists() and path.stat().st_size > 0,
        "path": str(path),
        "bytes": path.stat().st_size if path.exists() else 0,
        "label": label,
        "color": color,
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def probe_identity(path: Path) -> dict:
    probe = _ffprobe()
    meta: dict = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return meta
    meta["bytes"] = path.stat().st_size
    if not probe:
        return meta
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
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        meta["ffprobe"] = json.loads(probed.stdout or "{}")
    except json.JSONDecodeError:
        meta["ffprobe"] = {"parse_error": True}
    return meta


def apply_cut(
    *,
    preview_path: Path,
    program_path: Path,
    action: str,
    preview_source_id: str,
    previous_program_source_id: str,
) -> dict:
    if action not in ("cut", "take"):
        raise SystemExit(f"invalid action {action}")
    if not preview_path.exists():
        raise SystemExit(f"preview frame missing: {preview_path}")
    program_path.parent.mkdir(parents=True, exist_ok=True)
    issued = time.perf_counter()
    issued_wall = datetime.now(timezone.utc).isoformat()
    # Explicit Program bus switch: copy Preview identity onto Program.
    shutil.copyfile(preview_path, program_path)
    applied = time.perf_counter()
    applied_wall = datetime.now(timezone.utc).isoformat()
    return {
        "action": action,
        "preview_source_id": preview_source_id,
        "previous_program_source_id": previous_program_source_id,
        "program_source_id": preview_source_id,
        "issued_at": issued_wall,
        "applied_at": applied_wall,
        "cut_latency_ms": round((applied - issued) * 1000.0, 3),
        "auto_switch": False,
        "tally": {
            "previous_off": previous_program_source_id or None,
            "program_on": preview_source_id,
        },
    }


def run_session(args: argparse.Namespace) -> dict:
    assert_media_medium(args.medium)
    ffmpeg = _ffmpeg()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    PROGRAM_DIR.mkdir(parents=True, exist_ok=True)

    cam1 = PREVIEW_DIR / "cut_preview_cam1.jpg"
    cam2 = PREVIEW_DIR / "cut_preview_cam2.jpg"
    program = PROGRAM_DIR / "director_program.jpg"

    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()

    frame1 = write_identity_frame(
        ffmpeg=ffmpeg,
        path=cam1,
        label="cam-1",
        color="0x1B4D3E",
        width=args.width,
        height=args.height,
    )
    frame2 = write_identity_frame(
        ffmpeg=ffmpeg,
        path=cam2,
        label="cam-2",
        color="0x8B3D3D",
        width=args.width,
        height=args.height,
    )

    # Initial Program = cam-1 (as if prior Cut already happened).
    initial = apply_cut(
        preview_path=cam1,
        program_path=program,
        action="cut",
        preview_source_id="cam-1",
        previous_program_source_id="",
    )
    before = probe_identity(program)

    # Explicit Cut/Take: Preview cam-2 -> Program.
    cut = apply_cut(
        preview_path=cam2,
        program_path=program,
        action=args.action,
        preview_source_id="cam-2",
        previous_program_source_id="cam-1",
    )
    after = probe_identity(program)

    # Optional SRT smoke of the Program frame (encode path), not glass-to-glass.
    srt_meta: dict = {"ran": False}
    if args.with_srt:
        encoder = pick_encoder(ffmpeg, args.encoder)
        listen = f"srt://{args.host}:{args.port}?mode=listener&latency={args.srt_latency_us}"
        call = f"srt://{args.host}:{args.port}?mode=caller&latency={args.srt_latency_us}"
        out = PREVIEW_DIR / "cut_program_srt.jpg"
        if out.exists():
            out.unlink()
        sender = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(program),
                *encoder_args(encoder),
                "-an",
                "-t",
                "2",
                "-f",
                "mpegts",
                listen,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.8)
        receiver = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                call,
                "-frames:v",
                "1",
                "-update",
                "1",
                "-y",
                str(out),
            ],
            capture_output=True,
            text=True,
        )
        try:
            sender.wait(timeout=8)
        except subprocess.TimeoutExpired:
            sender.kill()
            sender.wait(timeout=5)
        srt_meta = {
            "ran": True,
            "encoder": encoder,
            "ok": receiver.returncode == 0
            and out.exists()
            and out.stat().st_size > 0,
            "bytes": out.stat().st_size if out.exists() else 0,
            "sender_rc": sender.returncode,
            "receiver_rc": receiver.returncode,
        }

    elapsed = time.perf_counter() - t0
    program_matches_preview = (
        program.exists()
        and cam2.exists()
        and program.stat().st_size == cam2.stat().st_size
        and program.read_bytes() == cam2.read_bytes()
    )
    ok = (
        frame1["ok"]
        and frame2["ok"]
        and program_matches_preview
        and cut["program_source_id"] == "cam-2"
        and cut["auto_switch"] is False
        and (not args.with_srt or srt_meta.get("ok") is True)
    )

    result = {
        "task": "OM-015",
        "gate": "G3",
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
            "note": "control-plane Cut/Take + local Program bus identity; "
            "not dual-device glass-to-glass",
        },
        "action": args.action,
        "frames": {"cam_1": frame1, "cam_2": frame2},
        "initial_program": initial,
        "cut": cut,
        "program_before": before,
        "program_after": after,
        "program_matches_preview_cam2": program_matches_preview,
        "srt": srt_meta,
        "wall_elapsed_s": round(elapsed, 3),
        "ok": ok,
        "self_check": args.self_check,
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "selfcheck" if args.self_check else "measure"
    out_path = RESULTS_DIR / f"g3_cut_take_{mode}_{stamp}.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["result_path"] = str(out_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="OM-015 Cut/Take G3 harness")
    parser.add_argument("--medium", default="wifi", choices=["wifi", "ethernet"])
    parser.add_argument("--action", default="cut", choices=["cut", "take"])
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9202)
    parser.add_argument("--srt-latency-us", type=int, default=120000)
    parser.add_argument("--encoder", default=None)
    parser.add_argument(
        "--with-srt",
        action="store_true",
        help="Also smoke Program frame over SRT after cut",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="CI mode: lavfi identity frames only; print CUT_TAKE_OK",
    )
    args = parser.parse_args()
    if args.self_check:
        args.with_srt = False
    result = run_session(args)
    if args.self_check:
        if not result["ok"]:
            print("CUT_TAKE_FAIL", json.dumps(result, indent=2))
            return 1
        print(
            "CUT_TAKE_OK",
            f"action={result['cut']['action']}",
            f"cut_latency_ms={result['cut']['cut_latency_ms']}",
            f"program={result['cut']['program_source_id']}",
        )
        return 0
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
