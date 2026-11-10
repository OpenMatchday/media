"""OCR confidence calibration (OM-031 / AC-5.2, AC-5.4).

D5 recorded Tesseract TSV word conf often 0.0 on successful digit-whitelist
reads (docs/spikes/d5-ocr-engine.md). Product confidence therefore prefers
raw TSV conf/100 when > 0, else digit-parse quality for the field shape.

Citations: tessdoc TSV output; PROJECT_SPEC AC-5.2 / AC-5.4.
"""

from __future__ import annotations

import re

# Authority gate used by app AuthorityStore (AC-5.4). Clean digit-parse
# fixtures score 1.0; empty/noise score below this threshold.
MIN_CONFIDENCE = 0.7

_CLOCK_RE = re.compile(r"^\d{1,2}:\d{2}$")
_INT_RE = re.compile(r"^\d{1,3}$")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def digits_only(text: str) -> str:
    return re.sub(r"[^0-9:]", "", text)


def digit_parse_quality(raw: str, field_id: str) -> float:
    """Quality in [0, 1] from parse shape when TSV conf is unavailable."""
    text = normalize_text(raw)
    if not text:
        return 0.0
    cleaned = digits_only(text)
    if not cleaned:
        return 0.0

    if field_id == "clock":
        if _CLOCK_RE.match(cleaned):
            return 1.0
        if ":" in cleaned and any(ch.isdigit() for ch in cleaned):
            return 0.45
        return 0.2

    # Numeric board fields: scores, period, fouls, shot_clock.
    if _INT_RE.match(cleaned) and cleaned == text.replace(" ", ""):
        return 1.0
    if _INT_RE.match(cleaned):
        return 0.85
    if any(ch.isdigit() for ch in cleaned):
        return 0.4
    return 0.0


def calibrate_confidence(
    *,
    raw_text: str,
    field_id: str,
    mean_word_conf: float | None,
) -> dict:
    """Return calibrated confidence and which signal was used.

    mean_word_conf: Tesseract TSV word conf average on 0–100 scale when present.
    """
    if mean_word_conf is not None and mean_word_conf > 0:
        conf = max(0.0, min(1.0, float(mean_word_conf) / 100.0))
        return {
            "confidence": round(conf, 4),
            "signal": "tesseract_tsv_conf",
            "mean_word_conf": float(mean_word_conf),
            "digit_parse_quality": digit_parse_quality(raw_text, field_id),
        }
    quality = digit_parse_quality(raw_text, field_id)
    return {
        "confidence": round(quality, 4),
        "signal": "digit_parse_quality",
        "mean_word_conf": float(mean_word_conf) if mean_word_conf is not None else None,
        "digit_parse_quality": quality,
    }
