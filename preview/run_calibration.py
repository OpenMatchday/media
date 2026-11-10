#!/usr/bin/env python3
"""OM-030: Four-corner calibration freeze, validate, homography, regions (AC-4.1–4.6).

Self-check uses a synthetic board plate (no live venue footage). Geometry follows
OpenCV getPerspectiveTransform 4-point form (h33=1):
https://docs.opencv.org/4.11.0/da/d54/group__imgproc__transform.html

Citations: PROJECT_SPEC AC-4.*; sports basketball/futsal scoreboards.json for
region field allowlists.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from calibration.geometry import (  # noqa: E402
    CornerSet,
    Point,
    apply_homography,
    board_homography,
    invert_homography,
    map_normalized_corners_to_display,
    normalize_corners,
    rectified_preview_size,
    validate_corners,
)
from calibration.regions import (  # noqa: E402
    BoardRegion,
    fields_for_sport,
    validate_regions,
)

PREVIEW_DIR = ROOT / "preview"
OUT_DIR = PREVIEW_DIR / "out" / "calibration"
RESULTS_DIR = ROOT / "measurements" / "results"


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg not found on PATH")
    return path


def freeze_frame(
    *,
    ffmpeg: str,
    path: Path,
    width: int,
    height: int,
    color: str = "0x1B3A2F",
) -> dict:
    """AC-4.1: capture/freeze a single frame for calibration."""
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
        "width": width,
        "height": height,
        "stderr_tail": (proc.stderr or "")[-400:],
    }


def sample_perspective_corners(width: int, height: int) -> CornerSet:
    """Synthetic board corners inset with mild perspective (fictional)."""
    return CornerSet(
        tl=Point(width * 0.18, height * 0.22),
        tr=Point(width * 0.82, height * 0.18),
        br=Point(width * 0.88, height * 0.78),
        bl=Point(width * 0.14, height * 0.72),
    )


def write_rectified_preview(
    *,
    ffmpeg: str,
    source: Path,
    dest: Path,
    corners: CornerSet,
    out_w: int,
    out_h: int,
) -> dict:
    """Warp freeze frame to a rectangle via ffmpeg perspective filter."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    # perspective=x0:y0:x1:y1:x2:y2:x3:y3 maps output corners TL,TR,BR,BL
    # from source coordinates (sense=destination).
    expr = (
        f"perspective="
        f"{corners.tl.x}:{corners.tl.y}:"
        f"{corners.tr.x}:{corners.tr.y}:"
        f"{corners.br.x}:{corners.br.y}:"
        f"{corners.bl.x}:{corners.bl.y}:"
        f"sense=destination:eval=init"
    )
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vf",
        f"{expr},scale={out_w}:{out_h}",
        "-frames:v",
        "1",
        "-y",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {
        "ok": proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0,
        "path": str(dest),
        "bytes": dest.stat().st_size if dest.exists() else 0,
        "width": out_w,
        "height": out_h,
        "stderr_tail": (proc.stderr or "")[-400:],
        "filter": "ffmpeg perspective sense=destination",
    }


def default_regions(sport_id: str) -> list[BoardRegion]:
    fields = fields_for_sport(sport_id)
    regions: list[BoardRegion] = []
    # Simple non-overlapping strips in unit board space (operator-editable in UI).
    layouts = {
        "home_score": (0.05, 0.15, 0.18, 0.25),
        "away_score": (0.77, 0.15, 0.18, 0.25),
        "clock": (0.35, 0.10, 0.30, 0.18),
        "shot_clock": (0.40, 0.35, 0.20, 0.15),
        "period": (0.45, 0.55, 0.10, 0.12),
        "home_fouls": (0.05, 0.70, 0.18, 0.15),
        "away_fouls": (0.77, 0.70, 0.18, 0.15),
    }
    for field_id in fields:
        box = layouts.get(field_id)
        if box is None:
            continue
        regions.append(
            BoardRegion(
                field_id=field_id,
                x=box[0],
                y=box[1],
                w=box[2],
                h=box[3],
            )
        )
    return regions


def run_self_check(*, measure: bool = False) -> dict:
    ffmpeg = _ffmpeg()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    frame_w, frame_h = 1280, 720
    freeze_path = OUT_DIR / "frozen_board.jpg"
    rectified_path = OUT_DIR / "rectified_board.jpg"

    freeze = freeze_frame(
        ffmpeg=ffmpeg,
        path=freeze_path,
        width=frame_w,
        height=frame_h,
    )
    corners = sample_perspective_corners(frame_w, frame_h)
    validation = validate_corners(
        corners,
        frame_width=frame_w,
        frame_height=frame_h,
    )
    h = board_homography(corners)
    # Corners should map to unit board TL/TR/BR/BL.
    mapped = [apply_homography(h, p).as_tuple() for p in corners.ordered()]
    expected = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    map_err = max(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(mapped, expected)
    )
    out_w, out_h = rectified_preview_size(corners, max_side=640)
    rectified = write_rectified_preview(
        ffmpeg=ffmpeg,
        source=freeze_path,
        dest=rectified_path,
        corners=corners,
        out_w=out_w,
        out_h=out_h,
    )

    # Invalid crossed corners must fail validation (bowtie TL-TR-BR-BL).
    bad = CornerSet(
        tl=Point(100, 100),
        tr=Point(500, 100),
        br=Point(100, 400),
        bl=Point(500, 400),
    )
    bad_validation = validate_corners(
        bad,
        frame_width=frame_w,
        frame_height=frame_h,
    )

    # AC-4.6 resilience: normalize at freeze, reproject at new scale/rotation.
    normalized = normalize_corners(corners, width=frame_w, height=frame_h)
    scaled = map_normalized_corners_to_display(
        normalized,
        display_width=1920,
        display_height=1080,
        rotation_degrees=0,
    )
    scaled_h = board_homography(scaled)
    scaled_mapped = [apply_homography(scaled_h, p).as_tuple() for p in scaled.ordered()]
    scale_err = max(
        abs(a[0] - b[0]) + abs(a[1] - b[1])
        for a, b in zip(scaled_mapped, expected)
    )
    rotated = map_normalized_corners_to_display(
        normalized,
        display_width=720,
        display_height=1280,
        rotation_degrees=90,
    )
    rotated_validation = validate_corners(
        rotated,
        frame_width=720,
        frame_height=1280,
    )
    # After 90° CW, labels still refer to board corners; winding in display may
    # change relative to screen axes — re-validate geometry via remapped homography.
    rot_h = board_homography(rotated)
    rot_mapped = [apply_homography(rot_h, p).as_tuple() for p in rotated.ordered()]
    rot_err = max(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(rot_mapped, expected)
    )

    sport_bb = "basketball/youth"
    regions = default_regions(sport_bb)
    regions_ok, region_reasons = validate_regions(regions, sport_id=sport_bb)
    # shot_clock must be omitted for youth, not faked.
    youth_fields = fields_for_sport(sport_bb)
    futsal_fields = fields_for_sport("futsal/fifa")

    inv = invert_homography(h)
    roundtrip = apply_homography(inv, apply_homography(h, corners.tl))
    roundtrip_err = abs(roundtrip.x - corners.tl.x) + abs(roundtrip.y - corners.tl.y)

    checks = {
        "freeze": freeze["ok"],
        "validation": validation.ok,
        "homography_map_err_lt_1e-6": map_err < 1e-6,
        "rectified_preview": rectified["ok"],
        "bad_corners_rejected": not bad_validation.ok,
        "scale_resilience_err_lt_1e-6": scale_err < 1e-6,
        "rotation_resilience_err_lt_1e-6": rot_err < 1e-6,
        "regions_ok": regions_ok,
        "youth_omits_shot_clock": "shot_clock" not in youth_fields,
        "futsal_omits_shot_clock": "shot_clock" not in futsal_fields,
        "roundtrip_err_lt_1e-4": roundtrip_err < 1e-4,
    }
    ok = all(checks.values())

    payload = {
        "task": "OM-030",
        "stamp": stamp,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "network": "n/a (local synthetic freeze; no network)",
        "sources": [
            "PROJECT_SPEC AC-4.1–AC-4.6",
            "OpenCV getPerspectiveTransform docs (4-point, h33=1)",
            "sports/packages/basketball/scoreboards.json",
            "sports/packages/futsal/scoreboards.json",
            "ffmpeg filters perspective",
        ],
        "freeze": freeze,
        "corners": corners.to_dict(),
        "validation": validation.to_dict(),
        "bad_validation": bad_validation.to_dict(),
        "homography": h,
        "mapped_unit_board": mapped,
        "map_err": map_err,
        "rectified": rectified,
        "scale_err": scale_err,
        "rotation_err": rot_err,
        "rotated_validation": rotated_validation.to_dict(),
        "roundtrip_err": roundtrip_err,
        "sport_id": sport_bb,
        "region_fields": youth_fields,
        "regions": [r.to_dict() for r in regions],
        "region_reasons": region_reasons,
        "futsal_fields": futsal_fields,
        "checks": checks,
        "ok": ok,
    }

    out_json = OUT_DIR / "calibration_selfcheck.json"
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if measure:
        measure_path = RESULTS_DIR / f"om030_calibration_selfcheck_{stamp}.json"
        measure_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        payload["measure_path"] = str(measure_path)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="OM-030 calibration harness")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--measure", action="store_true")
    args = parser.parse_args()
    if not args.self_check and not args.measure:
        parser.print_help()
        return 2
    payload = run_self_check(measure=args.measure or args.self_check)
    if payload["ok"]:
        print("CALIBRATION_OK")
        return 0
    print("CALIBRATION_FAIL")
    print(json.dumps(payload["checks"], indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
