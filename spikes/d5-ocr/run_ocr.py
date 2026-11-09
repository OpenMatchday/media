#!/usr/bin/env python3
"""OM-006 / D5: run on-device Tesseract on synthetic scoreboard fixtures."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
OUT = ROOT / "out"
RESULTS = ROOT / "results"
DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def find_tesseract() -> Path:
    env = os.environ.get("TESSERACT_CMD")
    if env and Path(env).exists():
        return Path(env)
    if DEFAULT_TESSERACT.exists():
        return DEFAULT_TESSERACT
    from shutil import which

    found = which("tesseract")
    if not found:
        raise SystemExit("tesseract not found; set TESSERACT_CMD")
    return Path(found)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def digits_only(text: str) -> str:
    return re.sub(r"[^0-9:]", "", text)


def field_match(expected: str, raw: str, field: str) -> bool:
    got = normalize_text(raw)
    if field == "composite":
        return expected.replace(" ", "") in got.replace(" ", "") or all(
            token in got for token in re.findall(r"[0-9:]+", expected)
        )
    if field in {"clock"}:
        return digits_only(expected) == digits_only(got) or expected in got
    # numeric regions: allow OCR to return the digits even with noise
    exp = digits_only(expected)
    got_d = digits_only(got)
    return exp == got_d or exp in got_d or expected in got


def mean_conf_from_tsv(tsv_path: Path) -> float | None:
    if not tsv_path.exists():
        return None
    lines = tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 2:
        return None
    header = lines[0].split("\t")
    try:
        conf_i = header.index("conf")
        text_i = header.index("text")
    except ValueError:
        return None
    confs: list[float] = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(conf_i, text_i):
            continue
        text = cols[text_i].strip()
        if not text:
            continue
        try:
            conf = float(cols[conf_i])
        except ValueError:
            continue
        if conf < 0:
            continue
        confs.append(conf)
    if not confs:
        return None
    return sum(confs) / len(confs)


def run_one(
    tess: Path,
    image: Path,
    meta: dict,
    tessdata_dir: Path | None,
    lang: str,
) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    stem = meta["id"]
    base = OUT / stem
    env = os.environ.copy()
    # Digit/colon whitelist helps scoreboard regions (clock/scores).
    # PSM 8 (single word) beats PSM 7 on sparse digit plates on this host.
    whitelist = "0123456789:"
    psm = "6" if meta["field"] == "composite" else "8"
    tessdata_args: list[str] = []
    if tessdata_dir is not None:
        tessdata_args = ["--tessdata-dir", str(tessdata_dir.resolve())]
    # Use -c create flags so a bare tessdata dir (traineddata only) still works.
    cmd = [
        str(tess),
        str(image),
        str(base),
        *tessdata_args,
        "-l",
        lang,
        "--psm",
        psm,
        "--oem",
        "1",
        "-c",
        f"tessedit_char_whitelist={whitelist}",
        "-c",
        "tessedit_create_tsv=1",
        "-c",
        "tessedit_create_txt=1",
    ]
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    txt_path = Path(str(base) + ".txt")
    tsv_path = Path(str(base) + ".tsv")
    raw = txt_path.read_text(encoding="utf-8", errors="replace") if txt_path.exists() else ""
    raw_n = normalize_text(raw)
    conf = mean_conf_from_tsv(tsv_path)
    ok = field_match(meta["expected"], raw_n, meta["field"]) if proc.returncode == 0 else False
    token_hits = {}
    if meta.get("expected_tokens"):
        for key, val in meta["expected_tokens"].items():
            token_hits[key] = val in raw_n or digits_only(val) in digits_only(raw_n)
        if meta["field"] == "composite":
            ok = all(token_hits.values())
    return {
        "id": meta["id"],
        "field": meta["field"],
        "image": image.name,
        "expected": meta["expected"],
        "raw_text": raw_n,
        "match": ok,
        "mean_word_conf": conf,
        "elapsed_ms": round(elapsed_ms, 3),
        "exit_code": proc.returncode,
        "stderr": (proc.stderr or "").strip()[:500],
        "token_hits": token_hits or None,
        "engine": {
            "name": "tesseract",
            "lang": lang,
            "psm": int(psm),
            "oem": 1,
            "whitelist": whitelist,
            "tessdata_dir": str(tessdata_dir.resolve()) if tessdata_dir else None,
        },
    }


def load_cases() -> list[dict]:
    cases = []
    for meta_path in sorted(FIXTURES.glob("*.json")):
        if meta_path.name == "manifest.json":
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        image = FIXTURES / meta["path"]
        if not image.exists():
            raise SystemExit(f"missing fixture image: {image}")
        cases.append((image, meta))
    return cases


def self_check(payload: dict) -> None:
    assert payload["engine"]["on_device"] is True
    assert payload["summary"]["fixture_count"] >= 6
    # Clock and scores are the critical AC-4/AC-5.2 fields for this spike.
    critical = {"clock_mmss", "shot_clock", "home_score", "away_score", "period", "fouls"}
    by_id = {row["id"]: row for row in payload["results"]}
    missing = critical - set(by_id)
    assert not missing, missing
    fails = [i for i in critical if not by_id[i]["match"]]
    assert not fails, f"critical fixture mismatches: {fails}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(RESULTS / "windows-tesseract-fixtures.json"))
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--tessdata-dir", default="")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    if not FIXTURES.exists() or not list(FIXTURES.glob("*.png")):
        raise SystemExit("fixtures missing; run make_fixtures.py first")

    tess = find_tesseract()
    tessdata_dir = Path(args.tessdata_dir) if args.tessdata_dir else None
    # Tesseract looks for eng.traineddata inside tessdata-dir (or TESSDATA_PREFIX/tessdata).
    if tessdata_dir is not None and (tessdata_dir / "eng.traineddata").exists():
        pass
    elif tessdata_dir is not None and (tessdata_dir / "tessdata" / "eng.traineddata").exists():
        tessdata_dir = tessdata_dir / "tessdata"
    results = []
    for image, meta in load_cases():
        results.append(run_one(tess, image, meta, tessdata_dir, args.lang))

    matched = sum(1 for r in results if r["match"])
    payload = {
        "device": platform.node(),
        "os": platform.platform(),
        "network": "n/a (local on-device OCR; no network required for recognize)",
        "engine": {
            "name": "tesseract",
            "path": str(tess),
            "on_device": True,
            "cloud": False,
        },
        "summary": {
            "fixture_count": len(results),
            "matched": matched,
            "accuracy_ratio": round(matched / len(results), 4) if results else 0.0,
        },
        "results": results,
        "notes": [
            "Synthetic PNG fixtures only; not live venue photos.",
            "Accuracy notes are fixture-local for engine choice; not product claims.",
            "AC-5.2 shape: value=raw_text, confidence=mean_word_conf, timestamp/staleness left to app authority layer.",
        ],
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if args.self_check:
        self_check(payload)
        print("SELF_CHECK_OK")
    return 0 if matched == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
