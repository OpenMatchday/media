# Media medium gate (OM-011 / AC-1.3)

Media streams use **Wi-Fi or Ethernet only**. Cellular and BLE are refused.

| Medium | Result |
|---|---|
| `wifi` | allowed |
| `ethernet` | allowed |
| `cellular` | refused |
| `ble` | refused |

Run:

```bash
python gates/media_medium.py
```

Expect `MEDIA_MEDIUM_GATE_OK`.

Live interface detection is deferred to capture/transport wiring; the Flutter
`MediaBridge.startMedia` / `startPreview` and native MethodChannel stubs enforce
the same allowlist before transport starts.

## One camera Preview (OM-013)

```bash
python gates/one_camera_preview.py
```

Expect `ONE_CAMERA_PREVIEW_OK`. Requires `ffmpeg` with SRT (`--enable-libsrt` or distro equivalent).
