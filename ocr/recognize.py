"""On-device region OCR via Tesseract (D5 / OM-031)."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .confidence import calibrate_confidence, normalize_text
from .reading import OcrReading, parse_field_value

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
        raise FileNotFoundError("tesseract not found; set TESSERACT_CMD")
    return Path(found)


def resolve_tessdata_dir(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        if (explicit / "eng.traineddata").exists():
            return explicit
        if (explicit / "tessdata" / "eng.traineddata").exists():
            return explicit / "tessdata"
        return explicit
    spike = (
        Path(__file__).resolve().parents[1]
        / "spikes"
        / "d5-ocr"
        / "tessdata_cache"
    )
    if (spike / "eng.traineddata").exists():
        return spike
    return None


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


def recognize_region(
    image: Path,
    *,
    field_id: str,
    stale: bool = False,
    tess: Path | None = None,
    tessdata_dir: Path | None = None,
    captured_at: datetime | None = None,
) -> tuple[OcrReading, dict]:
    tess_bin = tess or find_tesseract()
    data_dir = resolve_tessdata_dir(tessdata_dir)
    whitelist = "0123456789:"
    with tempfile.TemporaryDirectory(prefix="om031_ocr_") as tmp:
        base = Path(tmp) / "region"
        tessdata_args: list[str] = []
        if data_dir is not None:
            tessdata_args = ["--tessdata-dir", str(data_dir.resolve())]
        cmd = [
            str(tess_bin),
            str(image),
            str(base),
            *tessdata_args,
            "-l",
            "eng",
            "--psm",
            "8",
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
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        txt_path = Path(str(base) + ".txt")
        tsv_path = Path(str(base) + ".tsv")
        raw = txt_path.read_text(encoding="utf-8", errors="replace") if txt_path.exists() else ""
        raw_n = normalize_text(raw)
        mean_conf = mean_conf_from_tsv(tsv_path)
        calibrated = calibrate_confidence(
            raw_text=raw_n,
            field_id=field_id,
            mean_word_conf=mean_conf,
        )
        stamp = captured_at or datetime.now(timezone.utc)
        reading = OcrReading(
            field_id=field_id,
            value=parse_field_value(field_id, raw_n),
            confidence=float(calibrated["confidence"]),
            captured_at=stamp,
            stale=stale,
            raw_text=raw_n,
            confidence_signal=str(calibrated["signal"]),
        )
        meta = {
            "exit_code": proc.returncode,
            "elapsed_ms": round(elapsed_ms, 3),
            "mean_word_conf": mean_conf,
            "calibrated": calibrated,
            "stderr_tail": (proc.stderr or "")[-300:],
        }
        return reading, meta


def field_match(expected: str, raw: str, field_id: str) -> bool:
    got = normalize_text(raw)
    if field_id == "clock":
        return re.sub(r"[^0-9:]", "", expected) == re.sub(r"[^0-9:]", "", got) or expected in got
    exp = re.sub(r"[^0-9:]", "", expected)
    got_d = re.sub(r"[^0-9:]", "", got)
    return exp == got_d or exp in got_d or expected in got
