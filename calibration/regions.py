"""OCR region catalogs for calibrated boards (OM-030 / AC-4.5).

Field ids align with sports package scoreboards.json and app ScoreField wire
names. Variants omit fields that the sport package marks absent — never fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Wire ids used by app/protocol (ScoreField) and layout crop regions.
BASKETBALL_FIELDS = (
    "clock",
    "shot_clock",
    "home_score",
    "away_score",
    "period",
    "home_fouls",
    "away_fouls",
)

# Futsal has no shot clock in FIFA/youth packages; fouls use accumulated names
# in sports packages — map to operator ScoreField home_fouls/away_fouls.
FUTSAL_FIELDS = (
    "clock",
    "home_score",
    "away_score",
    "period",
    "home_fouls",
    "away_fouls",
)

# Basketball youth: shot clock optional / omitted before U14 (sports package).
BASKETBALL_YOUTH_FIELDS = tuple(f for f in BASKETBALL_FIELDS if f != "shot_clock")

SPORT_FIELDS: dict[str, tuple[str, ...]] = {
    "basketball": BASKETBALL_FIELDS,
    "basketball/fiba": BASKETBALL_FIELDS,
    "basketball/nba": BASKETBALL_FIELDS,
    "basketball/ncaa": BASKETBALL_FIELDS,
    "basketball/youth": BASKETBALL_YOUTH_FIELDS,
    "futsal": FUTSAL_FIELDS,
    "futsal/fifa": FUTSAL_FIELDS,
    "futsal/youth": FUTSAL_FIELDS,
}


@dataclass(frozen=True)
class BoardRegion:
    field_id: str
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return {
            "field_id": self.field_id,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
        }

    @staticmethod
    def from_dict(raw: dict) -> "BoardRegion":
        return BoardRegion(
            field_id=str(raw["field_id"]),
            x=float(raw["x"]),
            y=float(raw["y"]),
            w=float(raw["w"]),
            h=float(raw["h"]),
        )


def fields_for_sport(sport_id: str) -> tuple[str, ...]:
    key = sport_id.strip().lower()
    if key in SPORT_FIELDS:
        return SPORT_FIELDS[key]
    if "/" in key:
        base = key.split("/", 1)[0]
        if base in SPORT_FIELDS:
            return SPORT_FIELDS[base]
    raise ValueError(f"unknown_sport: {sport_id}")


def validate_regions(
    regions: Iterable[BoardRegion],
    *,
    sport_id: str,
) -> tuple[bool, list[str]]:
    allowed = set(fields_for_sport(sport_id))
    reasons: list[str] = []
    seen: set[str] = set()
    for region in regions:
        if region.field_id not in allowed:
            reasons.append(f"field_not_in_sport:{region.field_id}")
        if region.field_id in seen:
            reasons.append(f"duplicate_field:{region.field_id}")
        seen.add(region.field_id)
        if region.w <= 0 or region.h <= 0:
            reasons.append(f"non_positive_size:{region.field_id}")
        if region.x < 0 or region.y < 0 or region.x + region.w > 1.0 + 1e-9 or region.y + region.h > 1.0 + 1e-9:
            reasons.append(f"region_out_of_board:{region.field_id}")
    return (len(reasons) == 0, reasons)
