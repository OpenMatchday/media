#!/usr/bin/env python3
"""OM-006 / D5: create synthetic scoreboard PNGs with known labels (fictional)."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

# Fictional venue board values only. No real clubs or people.
CASES = [
    {
        "id": "clock_mmss",
        "label": "09:42",
        "field": "clock",
        "size": (320, 120),
        "font_size": 72,
    },
    {
        "id": "shot_clock",
        "label": "24",
        "field": "shot_clock",
        "size": (180, 120),
        "font_size": 80,
    },
    {
        "id": "home_score",
        "label": "78",
        "field": "score_home",
        "size": (180, 120),
        "font_size": 80,
    },
    {
        "id": "away_score",
        "label": "65",
        "field": "score_away",
        "size": (180, 120),
        "font_size": 80,
    },
    {
        "id": "period",
        "label": "2",
        "field": "period",
        "size": (120, 120),
        "font_size": 80,
    },
    {
        "id": "fouls",
        "label": "4",
        "field": "fouls",
        "size": (120, 120),
        "font_size": 80,
    },
    {
        "id": "board_composite",
        "label": "HOME 78  AWAY 65  09:42  P2  SC24",
        "field": "composite",
        "size": (900, 220),
        "font_size": 42,
        "multiline": False,
        "expected_tokens": {
            "score_home": "78",
            "score_away": "65",
            "clock": "09:42",
            "period": "2",
            "shot_clock": "24",
        },
    },
]


def load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\consola.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
        Path(r"C:\Windows\Fonts\arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def render_case(case: dict) -> Path:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    width, height = case["size"]
    image = Image.new("RGB", (width, height), color=(8, 12, 20))
    draw = ImageDraw.Draw(image)
    font = load_font(case["font_size"])
    text = case["label"]
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    xy = ((width - tw) / 2, (height - th) / 2 - bbox[1])
    draw.rectangle((8, 8, width - 9, height - 9), outline=(220, 220, 220), width=3)
    draw.text(xy, text, fill=(255, 230, 40), font=font)
    out = FIXTURES / f"{case['id']}.png"
    image.save(out, format="PNG")
    meta = {
        "id": case["id"],
        "path": out.name,
        "field": case["field"],
        "expected": case["label"],
        "expected_tokens": case.get("expected_tokens"),
        "width": width,
        "height": height,
        "note": "synthetic fictional scoreboard region; not a photo of a live venue",
    }
    (FIXTURES / f"{case['id']}.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return out


def main() -> int:
    written = [str(render_case(case).name) for case in CASES]
    manifest = {
        "fixture_count": len(written),
        "files": written,
        "fiction": True,
    }
    (FIXTURES / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
