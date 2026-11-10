"""OCR reading wire shape (AC-5.2): value, confidence, timestamp, staleness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class OcrReading:
    field_id: str
    value: Any
    confidence: float
    captured_at: datetime
    stale: bool
    raw_text: str = ""
    confidence_signal: str = ""

    def to_dict(self) -> dict:
        return {
            "field": self.field_id,
            "value": self.value,
            "confidence": self.confidence,
            "captured_at": self.captured_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "stale": self.stale,
            "raw_text": self.raw_text,
            "confidence_signal": self.confidence_signal,
        }

    def mark_stale(self) -> "OcrReading":
        return OcrReading(
            field_id=self.field_id,
            value=self.value,
            confidence=self.confidence,
            captured_at=self.captured_at,
            stale=True,
            raw_text=self.raw_text,
            confidence_signal=self.confidence_signal,
        )


def parse_field_value(field_id: str, raw_text: str) -> Any:
    """Best-effort typed value for authority plane; raw kept separately."""
    from .confidence import digits_only, normalize_text

    text = normalize_text(raw_text)
    cleaned = digits_only(text)
    if field_id == "clock":
        return cleaned if cleaned else text
    if not cleaned:
        return text
    # Prefer int when the field is numeric.
    digits = cleaned.replace(":", "")
    if digits.isdigit() and ":" not in cleaned:
        return int(digits)
    return cleaned
