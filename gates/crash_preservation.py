#!/usr/bin/env python3
"""CI/self-check entry for OM-019 crash preservation (AC-5.6)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "preview" / "run_crash_preservation.py"


def main() -> int:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--self-check"],
        cwd=str(ROOT),
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
