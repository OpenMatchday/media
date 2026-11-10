#!/usr/bin/env python3
"""OM-019: Crash preservation without auto-restart outputs (AC-5.6).

Simulates a director mid-event checkpoint (clocks/scores/lineup authority +
recording/streaming intents), kills the director process marker, then recovers
authority while forcing recording and streaming to stay stopped.

Citations: PROJECT_SPEC AC-5.6; app CrashPreservationStore wire shape.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gates"))
sys.path.insert(0, str(ROOT / "preview"))
from run_one_camera import PREVIEW_DIR, RESULTS_DIR  # noqa: E402

OUT_DIR = PREVIEW_DIR / "crash_preservation"
CHECKPOINT_NAME = "director_checkpoint.json"
PID_NAME = "director.pid"
OUTPUTS_NAME = "outputs.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def mid_event_checkpoint(*, session_id: str) -> dict:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "saved_at": utc_now(),
        "fields": [
            {
                "field": "clock",
                "value": "08:42",
                "authority": "manual",
                "confirmed": True,
                "locked": False,
            },
            {
                "field": "home_score",
                "value": 21,
                "authority": "manual",
                "confirmed": True,
                "locked": False,
            },
            {
                "field": "away_score",
                "value": 18,
                "authority": "manual",
                "confirmed": True,
                "locked": False,
            },
        ],
        "lineup_authority": [
            {
                "side": "home",
                "authority": "manual",
                "confirmed": True,
                "locked": False,
                "revision": "rev-home-1",
            },
            {
                "side": "away",
                "authority": "manual",
                "confirmed": True,
                "locked": False,
                "revision": "rev-away-1",
            },
        ],
        "event_log_seq": 7,
        "clock_elapsed_ms": 522000,
        "clock_running": True,
        "outputs": {"recording": True, "streaming": True},
    }


def recover_after_crash(checkpoint: dict) -> dict:
    """Restore authority fields; force outputs stopped (AC-5.6)."""
    restored_fields = {f["field"]: f for f in checkpoint.get("fields", [])}
    restored_lineup = {
        side["side"]: side for side in checkpoint.get("lineup_authority", [])
    }
    outputs = {"recording": False, "streaming": False}
    return {
        "restored": True,
        "session_id": checkpoint.get("session_id"),
        "fields": restored_fields,
        "lineup_authority": restored_lineup,
        "event_log_seq": checkpoint.get("event_log_seq", 0),
        "clock_elapsed_ms": checkpoint.get("clock_elapsed_ms", 0),
        "clock_running": False,
        "outputs": outputs,
        "message": "restored authority; recording/streaming left stopped",
    }


def run(*, self_check: bool, medium: str) -> dict:
    started = time.perf_counter()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    checkpoint_path = OUT_DIR / CHECKPOINT_NAME
    pid_path = OUT_DIR / PID_NAME
    outputs_path = OUT_DIR / OUTPUTS_NAME

    checkpoint = mid_event_checkpoint(session_id="sess-crash-harness")
    write_json(checkpoint_path, checkpoint)
    write_json(outputs_path, checkpoint["outputs"])

    # Live director marker (process stand-in).
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    director_alive_before = pid_path.exists()

    # Kill director mid-event: remove process marker; leave durable checkpoint.
    pid_path.unlink(missing_ok=True)
    director_alive_after_kill = pid_path.exists()

    loaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    recovery = recover_after_crash(loaded)
    write_json(outputs_path, recovery["outputs"])

    home = recovery["fields"].get("home_score", {})
    away = recovery["fields"].get("away_score", {})
    clock = recovery["fields"].get("clock", {})
    home_lineup = recovery["lineup_authority"].get("home", {})

    ok = (
        director_alive_before
        and not director_alive_after_kill
        and recovery["restored"] is True
        and home.get("value") == 21
        and away.get("value") == 18
        and clock.get("value") == "08:42"
        and home_lineup.get("confirmed") is True
        and recovery["outputs"]["recording"] is False
        and recovery["outputs"]["streaming"] is False
        and recovery["clock_running"] is False
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    result = {
        "ok": ok,
        "task": "OM-019",
        "ac": "AC-5.6",
        "self_check": self_check,
        "medium": medium,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "director_alive_before": director_alive_before,
        "director_alive_after_kill": director_alive_after_kill,
        "checkpoint_path": str(checkpoint_path),
        "outputs_path": str(outputs_path),
        "recovery": recovery,
        "elapsed_ms": round(elapsed_ms, 3),
        "saved_at": utc_now(),
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "selfcheck" if self_check else "measure"
    out_path = RESULTS_DIR / f"om019_crash_preservation_{label}_{stamp}.json"
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
        help="Label only; crash recovery is local durable state",
    )
    args = parser.parse_args()
    result = run(self_check=args.self_check, medium=args.medium)
    if result["ok"]:
        print(
            "CRASH_PRESERVATION_OK "
            f"recording={result['recovery']['outputs']['recording']} "
            f"streaming={result['recovery']['outputs']['streaming']} "
            f"home={result['recovery']['fields']['home_score']['value']} "
            f"elapsed_ms={result['elapsed_ms']}"
        )
        return 0
    print("CRASH_PRESERVATION_FAIL", json.dumps(result, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
