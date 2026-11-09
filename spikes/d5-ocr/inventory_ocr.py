#!/usr/bin/env python3
"""OM-006 / D5: inventory host OCR tooling, model sizes, and license citations."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"

DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_TESSDATA = Path(r"C:\Program Files\Tesseract-OCR\tessdata")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_tesseract() -> Path | None:
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return Path(env)
    if DEFAULT_TESSERACT.exists():
        return DEFAULT_TESSERACT
    from shutil import which

    found = which("tesseract")
    return Path(found) if found else None


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    tess = find_tesseract()
    payload: dict = {
        "device": platform.node(),
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "network": "n/a (local inventory)",
        "tesseract": None,
        "models": [],
        "citations": {
            "tesseract_license": "https://raw.githubusercontent.com/tesseract-ocr/tesseract/main/LICENSE",
            "tesseract_docs": "https://tesseract-ocr.github.io/tessdoc/",
            "tessdata_fast": "https://github.com/tesseract-ocr/tessdata_fast",
            "tessdata_fast_license": "https://github.com/tesseract-ocr/tessdata_fast/blob/main/LICENSE",
            "ml_kit_on_device": "https://developers.google.com/ml-kit",
            "ml_kit_text_v2_android": "https://developers.google.com/ml-kit/vision/text-recognition/v2/android",
            "apple_vision_text": "https://developer.apple.com/documentation/vision/recognizing_text_in_images",
            "windows_media_ocr": "https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr",
        },
        "platform_api_availability_this_host": {
            "Windows.Media.Ocr": "documented; requires package identity for desktop apps; not exercised this spike",
            "Apple Vision VNRecognizeTextRequest": "unavailable (no macOS/iOS host)",
            "ML Kit Text Recognition v2": "unavailable (no Android device/emulator run this spike)",
        },
    }
    if tess is None:
        payload["error"] = "tesseract not found"
        out = RESULTS / "ocr-inventory.json"
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload, indent=2))
        return 1

    proc = subprocess.run(
        [str(tess), "--version"], capture_output=True, text=True, check=False
    )
    version_text = (proc.stdout or proc.stderr or "").strip()
    tessdata = Path(os.environ.get("TESSDATA_PREFIX", DEFAULT_TESSDATA))
    models = []
    for name in ("eng.traineddata", "ita.traineddata", "osd.traineddata"):
        path = tessdata / name
        if not path.exists():
            continue
        models.append(
            {
                "name": name,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "source_note": "installed with host Tesseract package; re-check provenance vs tessdata / tessdata_fast before shipping",
            }
        )
    cache = ROOT / "tessdata_cache" / "eng.traineddata"
    if cache.exists():
        models.append(
            {
                "name": "eng.traineddata",
                "path": str(cache),
                "bytes": cache.stat().st_size,
                "sha256": sha256_file(cache),
                "source_note": "downloaded from tesseract-ocr/tessdata_fast (Apache-2.0)",
            }
        )

    payload["tesseract"] = {
        "path": str(tess),
        "version_output": version_text,
        "tessdata_prefix": str(tessdata),
        "exit_code": proc.returncode,
    }
    payload["models"] = models
    out = RESULTS / "ocr-inventory.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
