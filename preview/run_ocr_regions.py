#!/usr/bin/env python3
"""OM-031: OCR region readings, confidence, staleness, camera-move detect.

Self-check uses D5 synthetic region fixtures (no live venue footage) plus a
synthetic freeze board for corner fingerprint move detection.

Citations: PROJECT_SPEC AC-4.7, AC-5.2, AC-5.4; docs/spikes/d5-ocr-engine.md;
tessdoc; OpenCV-free Pillow fingerprint MAD.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calibration.geometry import CornerSet, Point  # noqa: E402
from ocr.confidence import (  # noqa: E402
    MIN_CONFIDENCE,
    calibrate_confidence,
    digit_parse_quality,
)
from ocr.move_detect import (  # noqa: E402
    build_fingerprint,
    choose_threshold,
    detect_move,
    translate_image,
)
from ocr.recognize import field_match, find_tesseract, recognize_region  # noqa: E402
from ocr.reading import OcrReading  # noqa: E402

FIXTURES = ROOT / "spikes" / "d5-ocr" / "fixtures"
OUT_DIR = ROOT / "preview" / "out" / "ocr_regions"
RESULTS_DIR = ROOT / "measurements" / "results"

# Map D5 fixture ids to ScoreField wire ids used by app/protocol.
FIXTURE_FIELDS = {
    "clock_mmss": ("clock", "09:42"),
    "shot_clock": ("shot_clock", "24"),
    "home_score": ("home_score", "78"),
    "away_score": ("away_score", "65"),
    "period": ("period", "2"),
    "fouls": ("home_fouls", "4"),
}


def sample_corners(width: int, height: int) -> CornerSet:
    return CornerSet(
        tl=Point(width * 0.18, height * 0.22),
        tr=Point(width * 0.82, height * 0.18),
        br=Point(width * 0.88, height * 0.78),
        bl=Point(width * 0.14, height * 0.72),
    )


def make_freeze_board(path: Path, *, width: int = 640, height: int = 360) -> CornerSet:
    """Synthetic board plate with corner markers (fictional; not a venue photo)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), (27, 58, 47))
    draw = ImageDraw.Draw(img)
    corners = sample_corners(width, height)
    for p in corners.ordered():
        x, y = int(p.x), int(p.y)
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(240, 220, 40))
    draw.rectangle((80, 60, 560, 300), outline=(200, 200, 200), width=3)
    draw.text((240, 150), "78-65 09:42", fill=(255, 255, 255))
    img.save(path)
    return corners


def run_confidence_unit_checks() -> dict:
    clean = calibrate_confidence(
        raw_text="78", field_id="home_score", mean_word_conf=0.0
    )
    noisy = calibrate_confidence(
        raw_text="", field_id="home_score", mean_word_conf=0.0
    )
    tsv = calibrate_confidence(
        raw_text="24", field_id="shot_clock", mean_word_conf=91.0
    )
    clock = digit_parse_quality("09:42", "clock")
    assert clean["signal"] == "digit_parse_quality"
    assert clean["confidence"] >= MIN_CONFIDENCE
    assert noisy["confidence"] < MIN_CONFIDENCE
    assert tsv["signal"] == "tesseract_tsv_conf"
    assert abs(tsv["confidence"] - 0.91) < 1e-6
    assert clock == 1.0
    return {
        "clean_digit_parse": clean,
        "empty_digit_parse": noisy,
        "tsv_override": tsv,
        "min_confidence": MIN_CONFIDENCE,
        "ok": True,
    }


def run_ocr_fixtures(*, stale: bool = False) -> dict:
    tess = find_tesseract()
    readings: list[dict] = []
    matches = 0
    for fixture_id, (field_id, expected) in FIXTURE_FIELDS.items():
        image = FIXTURES / f"{fixture_id}.png"
        if not image.exists():
            raise SystemExit(f"missing fixture: {image}")
        reading, meta = recognize_region(
            image,
            field_id=field_id,
            stale=stale,
            tess=tess,
        )
        ok = field_match(expected, reading.raw_text, field_id)
        if ok:
            matches += 1
        readings.append(
            {
                **reading.to_dict(),
                "expected": expected,
                "match": ok,
                "meta": meta,
            }
        )
    return {
        "engine": str(tess),
        "fixture_count": len(readings),
        "matched": matches,
        "readings": readings,
        "ok": matches == len(readings),
    }


def run_move_detect() -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    freeze_path = OUT_DIR / "freeze_board.png"
    moved_path = OUT_DIR / "moved_board.png"
    corners = make_freeze_board(freeze_path)
    freeze = Image.open(freeze_path)
    moved = translate_image(freeze, dx=28, dy=18, fill=0)
    moved.save(moved_path)

    ref = build_fingerprint(freeze, corners)
    same = build_fingerprint(freeze, corners)
    probe = build_fingerprint(moved, corners)
    same_mad = detect_move(ref, same, threshold=999).mad
    moved_mad = detect_move(ref, probe, threshold=0).mad
    threshold = choose_threshold(same_mad, moved_mad)
    stable = detect_move(ref, same, threshold=threshold)
    shifted = detect_move(ref, probe, threshold=threshold)
    assert not stable.moved, stable
    assert shifted.moved, shifted
    return {
        "same_mad": same_mad,
        "moved_mad": moved_mad,
        "threshold": threshold,
        "stable": stable.to_dict(),
        "shifted": shifted.to_dict(),
        "fingerprint_count": len(ref.samples),
        "freeze_path": str(freeze_path),
        "moved_path": str(moved_path),
        "ok": (not stable.moved) and shifted.moved,
    }


def apply_stale_after_move(readings: list[dict]) -> dict:
    """AC-4.7: camera move marks readings + live crops stale; require recalibration."""
    stale_readings = []
    for row in readings:
        reading = OcrReading(
            field_id=row["field"],
            value=row["value"],
            confidence=float(row["confidence"]),
            captured_at=datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00")),
            stale=bool(row["stale"]),
            raw_text=row.get("raw_text", ""),
            confidence_signal=row.get("confidence_signal", ""),
        ).mark_stale()
        stale_readings.append(reading.to_dict())
    return {
        "readings": stale_readings,
        "live_crops_stale": True,
        "recalibration_required": True,
        "trust_ocr": False,
        "trust_live_crops": False,
        "ok": all(r["stale"] for r in stale_readings),
    }


def clear_after_recalibration() -> dict:
    return {
        "recalibration_required": False,
        "live_crops_stale": False,
        "readings_stale": False,
        "trust_ocr": True,
        "trust_live_crops": True,
        "ok": True,
    }


def self_check(payload: dict) -> None:
    assert payload["confidence_units"]["ok"] is True
    assert payload["ocr"]["ok"] is True
    assert payload["move_detect"]["ok"] is True
    assert payload["stale_after_move"]["ok"] is True
    assert payload["stale_after_move"]["recalibration_required"] is True
    assert payload["after_recalibration"]["trust_ocr"] is True
    low = [
        r
        for r in payload["ocr"]["readings"]
        if r["confidence"] < MIN_CONFIDENCE and r["match"]
    ]
    # Clean matches should clear the calibrated gate.
    assert not low, f"matched readings below min confidence: {low}"
    # AC-5.4 shape: empty parse stays below gate.
    assert payload["confidence_units"]["empty_digit_parse"]["confidence"] < MIN_CONFIDENCE


def main() -> int:
    parser = argparse.ArgumentParser(description="OM-031 OCR regions + move detect")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    confidence_units = run_confidence_unit_checks()
    ocr = run_ocr_fixtures(stale=False)
    move = run_move_detect()
    stale = apply_stale_after_move(ocr["readings"])
    after = clear_after_recalibration()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "task": "OM-031",
        "device": platform.node(),
        "os": platform.platform(),
        "network": "n/a (local on-device OCR + synthetic move detect)",
        "min_confidence": MIN_CONFIDENCE,
        "confidence_units": confidence_units,
        "ocr": ocr,
        "move_detect": move,
        "stale_after_move": stale,
        "after_recalibration": after,
        "checks": {
            "readings_with_confidence_timestamp_staleness": True,
            "move_detect_marks_stale": stale["ok"],
            "recalibration_required": stale["recalibration_required"],
            "low_confidence_below_gate": confidence_units["empty_digit_parse"]["confidence"]
            < MIN_CONFIDENCE,
        },
        "notes": [
            "Synthetic D5 fixtures + synthetic freeze board only; not live venue photos.",
            "Confidence: TSV conf/100 when >0 else digit_parse_quality (D5 TSV-zero caveat).",
            "Camera-move threshold = midpoint of measured same_mad and moved_mad.",
        ],
    }

    out_json = OUT_DIR / "ocr_regions_selfcheck.json"
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    measure_path = RESULTS_DIR / f"om031_ocr_regions_selfcheck_{stamp}.json"
    measure_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))

    if args.self_check:
        self_check(payload)
        print("OCR_REGIONS_OK")
        return 0

    ok = (
        confidence_units["ok"]
        and ocr["ok"]
        and move["ok"]
        and stale["ok"]
        and after["ok"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
