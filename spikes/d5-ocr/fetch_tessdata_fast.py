#!/usr/bin/env python3
"""Download tessdata_fast eng.traineddata for size/license comparison (optional)."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "tessdata_cache"
RESULTS = ROOT / "results"

# Official tessdata_fast eng model (Apache-2.0).
URL = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata"
# Size from GitHub Contents API for main/eng.traineddata (recorded this spike).
EXPECTED_BYTES = 4113088


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    dest = CACHE / "eng.traineddata"
    if not dest.exists() or dest.stat().st_size != EXPECTED_BYTES:
        print(f"downloading {URL}")
        urllib.request.urlretrieve(URL, dest)
    size = dest.stat().st_size
    digest = sha256_file(dest)
    payload = {
        "url": URL,
        "path": str(dest),
        "bytes": size,
        "sha256": digest,
        "expected_bytes_github_blob": EXPECTED_BYTES,
        "size_match": size == EXPECTED_BYTES,
        "license": "Apache-2.0 (tessdata_fast LICENSE)",
        "license_url": "https://github.com/tesseract-ocr/tessdata_fast/blob/main/LICENSE",
    }
    out = RESULTS / "tessdata-fast-eng.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["size_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
