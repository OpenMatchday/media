#!/usr/bin/env python3
"""OM-017: Connection health + Program source-loss fallback (AC-2.7 / AC-2.8).

Simulates two lavfi camera plates, marks health connected/degraded/lost with
timestamps, drops the Program source, and applies a configured safe state
(last_frame_freeze, slate, or alternate_source) without ending the session.

Citations: protocol schemas/v0/common.json HealthState; session.json
program_fallback; ffmpeg lavfi color (ffmpeg-filters).
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
from run_one_camera import PREVIEW_DIR, RESULTS_DIR, _ffmpeg  # noqa: E402

OUT_DIR = PREVIEW_DIR / "source_health"
PROGRAM_PATH = OUT_DIR / "program.jpg"
SLATE_PATH = OUT_DIR / "slate.jpg"
CAM1_PATH = OUT_DIR / "cam-1.jpg"
CAM2_PATH = OUT_DIR / "cam-2.jpg"

VALID_FALLBACKS = ("last_frame_freeze", "slate", "alternate_source")
VALID_STATES = ("connected", "degraded", "lost")


def write_plate(
    *,
    ffmpeg: str,
    path: Path,
    color: str,
    width: int,
    height: int,
) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
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
        "color": color,
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def health_entry(source_id: str, state: str, observed_at: str, detail: str = "") -> dict:
    if state not in VALID_STATES:
        raise SystemExit(f"invalid health state: {state}")
    entry = {
        "source_id": source_id,
        "state": state,
        "observed_at": observed_at,
    }
    if detail:
        entry["detail"] = detail
    return entry


def apply_fallback(
    *,
    mode: str,
    lost_source_id: str,
    program_before: Path,
    cam1: Path,
    cam2: Path,
    slate: Path,
    program_after: Path,
    alternate_source_id: str,
) -> dict:
    if mode not in VALID_FALLBACKS:
        raise SystemExit(f"invalid fallback: {mode}")
    program_after.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    if mode == "last_frame_freeze":
        if not program_before.exists():
            raise SystemExit("program frame missing before freeze")
        shutil.copyfile(program_before, program_after)
        note = "Program held on last frame freeze"
        next_source = lost_source_id
    elif mode == "slate":
        if not slate.exists():
            raise SystemExit("slate plate missing")
        shutil.copyfile(slate, program_after)
        note = "Program switched to slate"
        next_source = "slate"
    else:
        alt_path = cam2 if alternate_source_id == "cam-2" else cam1
        if alternate_source_id == lost_source_id:
            raise SystemExit("alternate source must differ from lost source")
        if not alt_path.exists():
            raise SystemExit(f"alternate plate missing: {alt_path}")
        shutil.copyfile(alt_path, program_after)
        note = f"Program switched to alternate source {alternate_source_id}"
        next_source = alternate_source_id
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    return {
        "program_fallback": mode,
        "lost_source_id": lost_source_id,
        "program_source_id": next_source,
        "program_path": str(program_after),
        "program_bytes": program_after.stat().st_size if program_after.exists() else 0,
        "fallback_apply_ms": elapsed_ms,
        "session_alive": True,
        "message": note,
        "ok": program_after.exists() and program_after.stat().st_size > 0,
    }


def run_session(args: argparse.Namespace) -> dict:
    assert_media_medium(args.medium)
    ffmpeg = _ffmpeg()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()

    cam1 = write_plate(
        ffmpeg=ffmpeg,
        path=CAM1_PATH,
        color="0x1B4D3E",
        width=args.width,
        height=args.height,
    )
    cam2 = write_plate(
        ffmpeg=ffmpeg,
        path=CAM2_PATH,
        color="0x3D5A8B",
        width=args.width,
        height=args.height,
    )
    slate = write_plate(
        ffmpeg=ffmpeg,
        path=SLATE_PATH,
        color="0x202020",
        width=args.width,
        height=args.height,
    )
    if not (cam1["ok"] and cam2["ok"] and slate["ok"]):
        return {
            "task": "OM-017",
            "ok": False,
            "error": "plate write failed",
            "plates": {"cam-1": cam1, "cam-2": cam2, "slate": slate},
        }

    # Program starts on cam-1.
    shutil.copyfile(CAM1_PATH, PROGRAM_PATH)
    program_before_bytes = PROGRAM_PATH.stat().st_size
    observed_connected = datetime.now(timezone.utc).isoformat()
    health_timeline = [
        health_entry("cam-1", "connected", observed_connected),
        health_entry("cam-2", "connected", observed_connected),
    ]

    time.sleep(max(args.degrade_delay_s, 0.0))
    observed_degraded = datetime.now(timezone.utc).isoformat()
    health_timeline.append(
        health_entry(
            "cam-1",
            "degraded",
            observed_degraded,
            detail="simulated packet loss rising",
        )
    )

    time.sleep(max(args.drop_delay_s, 0.0))
    # Drop cam-1: remove its live plate but keep the Program freeze copy.
    if CAM1_PATH.exists():
        CAM1_PATH.unlink()
    observed_lost = datetime.now(timezone.utc).isoformat()
    health_timeline.append(
        health_entry("cam-1", "lost", observed_lost, detail="camera drop test")
    )

    program_before = OUT_DIR / "program_before_drop.jpg"
    shutil.copyfile(PROGRAM_PATH, program_before)
    fallback = apply_fallback(
        mode=args.fallback,
        lost_source_id="cam-1",
        program_before=program_before,
        cam1=CAM1_PATH,
        cam2=CAM2_PATH,
        slate=SLATE_PATH,
        program_after=PROGRAM_PATH,
        alternate_source_id=args.alternate_source_id,
    )

    # Session remains alive: health still readable after drop.
    post_drop_health = [
        health_entry("cam-1", "lost", observed_lost, detail="camera drop test"),
        health_entry(
            "cam-2",
            "connected",
            datetime.now(timezone.utc).isoformat(),
        ),
    ]

    states_seen = {entry["state"] for entry in health_timeline}
    ok = (
        fallback.get("ok") is True
        and fallback.get("session_alive") is True
        and states_seen >= {"connected", "degraded", "lost"}
        and PROGRAM_PATH.exists()
        and PROGRAM_PATH.stat().st_size > 0
        and all("observed_at" in entry for entry in post_drop_health)
    )
    if args.fallback == "last_frame_freeze":
        ok = ok and PROGRAM_PATH.stat().st_size == program_before_bytes
    elif args.fallback == "slate":
        ok = ok and PROGRAM_PATH.stat().st_size == SLATE_PATH.stat().st_size
    elif args.fallback == "alternate_source":
        ok = ok and PROGRAM_PATH.stat().st_size == CAM2_PATH.stat().st_size

    elapsed = time.perf_counter() - t0
    result = {
        "task": "OM-017",
        "acs": ["AC-2.7", "AC-2.8"],
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
            "note": "local lavfi plates + filesystem drop; not dual-device field loss",
        },
        "method": {
            "plates": "ffmpeg lavfi color stills for cam-1/cam-2/slate",
            "drop": "unlink cam-1 plate after Program copy",
            "fallback": args.fallback,
            "citations": [
                "protocol/schemas/v0/common.json HealthState",
                "protocol/schemas/v0/session.json program_fallback",
                "https://ffmpeg.org/ffmpeg-filters.html#color",
            ],
        },
        "config": {
            "fallback": args.fallback,
            "alternate_source_id": args.alternate_source_id,
            "width": args.width,
            "height": args.height,
            "degrade_delay_s": args.degrade_delay_s,
            "drop_delay_s": args.drop_delay_s,
        },
        "plates": {"cam-1": cam1, "cam-2": cam2, "slate": slate},
        "health_timeline": health_timeline,
        "post_drop_health": post_drop_health,
        "fallback": fallback,
        "summary": {
            "session_alive": True,
            "states_seen": sorted(states_seen),
            "program_bytes_after": PROGRAM_PATH.stat().st_size
            if PROGRAM_PATH.exists()
            else 0,
            "fallback_apply_ms": fallback.get("fallback_apply_ms"),
            "wall_s": round(elapsed, 3),
        },
        "wall_elapsed_s": round(elapsed, 3),
        "ok": ok,
        "self_check": args.self_check,
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = "selfcheck" if args.self_check else "measure"
    out_path = RESULTS_DIR / f"om017_source_health_{mode}_{stamp}.json"
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["result_path"] = str(out_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OM-017 connection health and source-loss fallback"
    )
    parser.add_argument("--medium", default="wifi", choices=["wifi", "ethernet"])
    parser.add_argument(
        "--fallback",
        default="last_frame_freeze",
        choices=list(VALID_FALLBACKS),
    )
    parser.add_argument("--alternate-source-id", default="cam-2")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=240)
    parser.add_argument("--degrade-delay-s", type=float, default=0.05)
    parser.add_argument("--drop-delay-s", type=float, default=0.05)
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="CI mode: run freeze + slate + alternate; print SOURCE_HEALTH_OK",
    )
    args = parser.parse_args()

    if args.self_check:
        args.degrade_delay_s = min(args.degrade_delay_s, 0.05)
        args.drop_delay_s = min(args.drop_delay_s, 0.05)
        modes = ("last_frame_freeze", "slate", "alternate_source")
        results = []
        all_ok = True
        for mode in modes:
            mode_args = argparse.Namespace(**vars(args))
            mode_args.fallback = mode
            mode_args.self_check = True
            one = run_session(mode_args)
            results.append(
                {
                    "fallback": mode,
                    "ok": one.get("ok"),
                    "result_path": one.get("result_path"),
                    "session_alive": one.get("summary", {}).get("session_alive"),
                    "fallback_apply_ms": one.get("summary", {}).get(
                        "fallback_apply_ms"
                    ),
                }
            )
            all_ok = all_ok and bool(one.get("ok"))
        summary = {
            "task": "OM-017",
            "ok": all_ok,
            "modes": results,
            "self_check": True,
        }
        if not all_ok:
            print("SOURCE_HEALTH_FAIL", json.dumps(summary, indent=2))
            return 1
        apply_ms = [
            m["fallback_apply_ms"]
            for m in results
            if m.get("fallback_apply_ms") is not None
        ]
        print(
            "SOURCE_HEALTH_OK",
            f"modes={len(results)}",
            f"session_alive=true",
            f"max_fallback_apply_ms={max(apply_ms) if apply_ms else None}",
        )
        return 0

    result = run_session(args)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
