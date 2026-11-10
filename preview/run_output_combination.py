#!/usr/bin/env python3
"""OM-026: Output combination enablement / G7 sustain harness (AC-3.1, AC-3.11).

Records whether a director-class host can sustain recording + live aspects
among 16:9, 9:16, and 1:1 for a short rehearsal segment. Release enablement
still requires capability-matrix cells to be verified/partial after review
(OM-047); this harness never invents verified status.

Citations:
- PROJECT_SPEC AC-3.1, AC-3.11; ARCHITECTURE.md G7
- Aspect sizes: common HD/UHD framing (16:9 1280x720, 9:16 720x1280, 1:1 720x720)
- ffmpeg lavfi testsrc / libx264 host-tool encode (measurement only; not product codec)
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
from run_one_camera import PREVIEW_DIR, RESULTS_DIR  # noqa: E402

OUT_DIR = PREVIEW_DIR / "output_combination"
ASPECTS = ("16:9", "9:16", "1:1")
ASPECT_SIZE = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (720, 720),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg not found on PATH")
    return path


def combination_key(*, recording: bool, live_aspects: list[str]) -> str:
    aspects = sorted(set(live_aspects))
    parts = (["recording"] if recording else []) + aspects
    return "none" if not parts else "+".join(parts)


def release_allows(
    *,
    recording: bool,
    live_aspects: list[str],
    aspect_flags: dict[str, str],
    recording_flag: str,
    combination_flag: str,
) -> bool:
    """Mirror app CapabilityMatrix.allowsOutputCombination for release builds."""
    if not recording and not live_aspects:
        return True

    def ok(state: str) -> bool:
        return state in ("verified", "partial")

    if recording and not ok(recording_flag):
        return False
    for aspect in live_aspects:
        if not ok(aspect_flags.get(aspect, "unverified")):
            return False
    return ok(combination_flag)


def run_sustain(
    *,
    recording: bool,
    live_aspects: list[str],
    seconds: float,
) -> dict:
    ffmpeg = _ffmpeg()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    outputs: list[dict] = []
    procs: list[tuple[str, subprocess.Popen, Path, float]] = []

    def start(label: str, width: int, height: int, path: Path) -> None:
        started = time.perf_counter()
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
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        procs.append((label, proc, path, started))

    wall_start = time.perf_counter()
    if recording:
        start("recording", 1280, 720, OUT_DIR / f"recording_{stamp}.mp4")
    for aspect in live_aspects:
        w, h = ASPECT_SIZE[aspect]
        safe = aspect.replace(":", "x")
        start(f"live_{safe}", w, h, OUT_DIR / f"live_{safe}_{stamp}.mp4")

    for label, proc, path, started in procs:
        stdout, stderr = proc.communicate()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        size = path.stat().st_size if path.exists() else 0
        outputs.append(
            {
                "label": label,
                "path": str(path),
                "ok": proc.returncode == 0 and size > 0,
                "exit": proc.returncode,
                "bytes": size,
                "elapsed_ms": round(elapsed_ms, 3),
                "stderr_tail": (stderr or "")[-400:],
            }
        )

    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    sustained = bool(outputs) and all(item["ok"] for item in outputs)
    return {
        "recording": recording,
        "live_aspects": live_aspects,
        "combination_key": combination_key(
            recording=recording, live_aspects=live_aspects
        ),
        "segment_seconds": seconds,
        "wall_ms": round(wall_ms, 3),
        "outputs": outputs,
        "sustained": sustained,
    }


def run(*, self_check: bool, medium: str, seconds: float) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Default matrix flags remain unverified until OM-047 promotes them.
    aspect_flags = {aspect: "unverified" for aspect in ASPECTS}
    recording_flag = "unverified"
    combination_flag = "unverified"

    gate_cases = [
        {
            "name": "empty",
            "recording": False,
            "live_aspects": [],
            "expect_release": True,
        },
        {
            "name": "recording_plus_16x9_unverified",
            "recording": True,
            "live_aspects": ["16:9"],
            "expect_release": False,
        },
        {
            "name": "all_aspects_unverified",
            "recording": True,
            "live_aspects": list(ASPECTS),
            "expect_release": False,
        },
        {
            "name": "measured_partial_combo",
            "recording": True,
            "live_aspects": ["16:9", "9:16"],
            "aspect_flags": {
                "16:9": "verified",
                "9:16": "partial",
                "1:1": "unverified",
            },
            "recording_flag": "verified",
            "combination_flag": "partial",
            "expect_release": True,
        },
        {
            "name": "combo_verified_aspect_missing",
            "recording": True,
            "live_aspects": ["16:9", "1:1"],
            "aspect_flags": {"16:9": "verified", "1:1": "unverified"},
            "recording_flag": "verified",
            "combination_flag": "verified",
            "expect_release": False,
        },
    ]

    gate_results = []
    gates_ok = True
    for case in gate_cases:
        allowed = release_allows(
            recording=case["recording"],
            live_aspects=case["live_aspects"],
            aspect_flags=case.get("aspect_flags", aspect_flags),
            recording_flag=case.get("recording_flag", recording_flag),
            combination_flag=case.get("combination_flag", combination_flag),
        )
        matched = allowed == case["expect_release"]
        gates_ok = gates_ok and matched
        gate_results.append(
            {
                "name": case["name"],
                "allowed": allowed,
                "expect_release": case["expect_release"],
                "ok": matched,
            }
        )

    # Sustain attempt: recording + all three live aspects for short segment.
    sustain = run_sustain(
        recording=True,
        live_aspects=list(ASPECTS),
        seconds=seconds,
    )

    # Support-flag inventory recorded honestly as unverified (no auto-promote).
    support_flags = {
        "recording": recording_flag,
        "aspects": aspect_flags,
        "multi_output_2plus": "unverified",
        "note": (
            "Flags stay unverified until director-class sustain is reviewed "
            "(AC-3.11 / G7 / OM-047). Harness sustain != release enablement."
        ),
    }

    ok = gates_ok and sustain["sustained"]
    result = {
        "ok": ok,
        "task": "OM-026",
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
        "support_flags": support_flags,
        "gate_results": gate_results,
        "sustain": sustain,
        "release_enablement": False,
        "saved_at": utc_now(),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "selfcheck" if self_check else "measure"
    out_path = RESULTS_DIR / f"om026_output_combination_{label}_{stamp}.json"
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
        default=2.0,
        help="Rehearsal segment length for concurrent encodes",
    )
    args = parser.parse_args()
    result = run(
        self_check=args.self_check,
        medium=args.medium,
        seconds=args.seconds,
    )
    if result["ok"]:
        print(
            "OUTPUT_COMBINATION_OK "
            f"sustained={result['sustain']['sustained']} "
            f"key={result['sustain']['combination_key']} "
            f"release_enablement={result['release_enablement']} "
            f"wall_ms={result['sustain']['wall_ms']}"
        )
        return 0
    print("OUTPUT_COMBINATION_FAIL", json.dumps(result, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
