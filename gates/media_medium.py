#!/usr/bin/env python3
"""Media plane medium gate (AC-1.3).

Allowed: wifi, ethernet.
Refused: cellular, ble (and aliases). Media never uses BLE or cellular.

Official citations for live transport detection (wired later in capture path):
- Android NetworkCapabilities.hasTransport
  https://developer.android.com/reference/android/net/NetworkCapabilities
- Windows NetworkInformation.GetInternetConnectionProfile
  https://learn.microsoft.com/en-us/uwp/api/windows.networking.connectivity.networkinformation.getinternetconnectionprofile
"""

from __future__ import annotations

ALLOWED = frozenset({"wifi", "ethernet"})
REFUSED = frozenset({"cellular", "ble", "bluetooth", "bt"})


def assert_media_medium(medium: str) -> None:
    if medium in ALLOWED:
        return
    raise ValueError(
        f'Media streams use Wi-Fi or Ethernet only; refused medium="{medium}"'
    )


def is_allowed(medium: str) -> bool:
    return medium in ALLOWED


def main() -> int:
    for medium in sorted(ALLOWED):
        assert_media_medium(medium)
        assert is_allowed(medium)
    for medium in sorted(REFUSED):
        try:
            assert_media_medium(medium)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected refuse for {medium}")
        assert not is_allowed(medium)
    print("MEDIA_MEDIUM_GATE_OK allowed=" + ",".join(sorted(ALLOWED)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
