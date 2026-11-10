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

## Two cameras + independent audio (OM-014)

```bash
python gates/two_cameras_audio.py
```

Expect `TWO_CAMERAS_AUDIO_OK`. Self-check uses two lavfi `testsrc` paths plus lavfi `sine` audio meters.

## Cut/Take G3 (OM-015)

```bash
python gates/cut_take.py
```

Expect `CUT_TAKE_OK`.

## A/V sync + drift G5 (OM-016)

```bash
python gates/av_sync.py
```

Expect `AV_SYNC_OK`. Self-check injects lavfi audio `itsoffset`, measures
ffprobe `start_time` delta, applies inverse correction, and records residual.

## Source health + fallback (OM-017)

```bash
python gates/source_health.py
```

Expect `SOURCE_HEALTH_OK`. Self-check walks connected/degraded/lost, drops
cam-1, and applies freeze/slate/alternate Program safe states without ending
the session.

## Output combination G7 (OM-026)

```bash
python gates/output_combination.py
```

Expect `OUTPUT_COMBINATION_OK`. Self-check verifies release gates for
`16:9` / `9:16` / `1:1` support flags plus concurrent lavfi sustain of
recording + three live aspects. Does not promote capability-matrix cells.
