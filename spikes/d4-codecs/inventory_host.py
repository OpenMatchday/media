#!/usr/bin/env python3
"""Record host GPU/CPU inventory for OM-005 encode matrix (Windows/WSL notes)."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def powershell(script: str) -> str:
    proc = run(["powershell", "-NoProfile", "-Command", script])
    return (proc.stdout or "").strip()


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "spike": "OM-005 / D4",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "node": platform.node(),
        },
    }

    if platform.system() == "Windows":
        cpu = powershell(
            "Get-CimInstance Win32_Processor | Select-Object Name,Manufacturer,NumberOfCores | ConvertTo-Json -Compress"
        )
        gpu = powershell(
            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,AdapterCompatibility,PNPDeviceID,Status | ConvertTo-Json -Compress"
        )
        os_caption = powershell(
            "(Get-CimInstance Win32_OperatingSystem).Caption; (Get-CimInstance Win32_OperatingSystem).Version"
        )
        payload["windows"] = {
            "os_caption": os_caption,
            "cpu_json": cpu,
            "gpu_json": gpu,
        }
        # Best-effort parse
        try:
            payload["cpu"] = json.loads(cpu) if cpu else None
        except json.JSONDecodeError:
            payload["cpu"] = cpu
        try:
            payload["gpu"] = json.loads(gpu) if gpu else None
        except json.JSONDecodeError:
            payload["gpu"] = gpu

    out = RESULTS / "host-inventory.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
