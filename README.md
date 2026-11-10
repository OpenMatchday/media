# Open Matchday -- media

Capture, encode, transport, decode, compositing, audio, recording, and streaming

Part of the [OpenMatchday](https://github.com/OpenMatchday) organization.

## Notes (OM-003 / D2)

- Evaluated upstream: [xtool-org/xtool](https://github.com/xtool-org/xtool) 1.24.0 (MIT) — iOS device install/signing helper on Linux/WSL; **not** a media capture/encode/transport library.
- No `media` code dependency on xtool. Flutter `app` iOS builds still require macOS/`xcodebuild` (xtool does not build Flutter `.xcodeproj`; see docs spike D2).
- Full spike record: sibling `docs` repo `spikes/d2-xtool-xtools.md`.

## Notes (OM-004 / D3)

- Provisional media transport: **SRT** (Haivision libsrt MPL-2.0 intended production library). Control plane stays in `protocol`.
- Spike harness and JSON results: `spikes/d3-transport/`. Full write-up: sibling `docs` repo `spikes/d3-media-transport.md`.
- Numbers in results are transport-only shared-clock probes on one Windows host — not glass-to-glass product targets.

## Notes (OM-005 / D4)

- Provisional encode path: **hardware first** (platform/vendor APIs); software fallback **OpenH264** (BSD). libx264 remains host-tool only (GPL-2.0).
- Spike harness and JSON results: `spikes/d4-codecs/`. Full write-up: sibling `docs` repo `spikes/d4-codecs-encoders.md`.
- This Windows AMD host: AMF + Media Foundation encode smokes PASS; NVENC/QSV/VA-API FAIL or unavailable. No live camera capture in this spike.

## Notes (OM-006 / D5)

- Provisional OCR engine: **Tesseract 5** + **tessdata_fast eng** (Apache-2.0), on-device only; no cloud OCR.
- Spike harness and JSON results: `spikes/d5-ocr/`. Full write-up: sibling `docs` repo `spikes/d5-ocr-engine.md`.
- Model size recorded: `eng.traineddata` 4,113,088 bytes. Synthetic fixture accuracy 7/7 on this Windows host; confidence calibration still open.

## Notes (OM-011 / AC-1.3)

- Media plane allowlist: **wifi** and **ethernet** only. Cellular and BLE are refused.
- Gate script: `gates/media_medium.py` (`MEDIA_MEDIUM_GATE_OK`). Enforced in Flutter `MediaBridge.startMedia` and native MethodChannel stubs before transport starts.
- Live NIC/transport detection deferred to capture path; citations in `gates/README.md`.

## Notes (OM-013 / one camera Preview)

- Path: capture -> encode (hardware preferred) -> SRT -> director receive snapshot.
- Harness: `preview/run_one_camera.py` (`ONE_CAMERA_PREVIEW_OK` via `--self-check`).
- Measurement log started under `measurements/` (G1/G2 start; dual-device glass-to-glass still pending).
- App Director Preview bus control is in sibling `app`; this repo owns frames/transport.

## Notes (OM-014 / two cameras + independent audio)

- Two Preview paths (cam-1 / cam-2) over SRT on adjacent ports; snapshots under `preview/out/`.
- Harness: `preview/run_two_cameras_audio.py` (`TWO_CAMERAS_AUDIO_OK` via `--self-check`).
- Audio source is selectable and recorded as not following video Program (AC-2.4); peak/RMS meters via ffmpeg `astats` (AC-2.5).
- If only one usable dshow video device is present, cam-2 falls back to lavfi `testsrc` and that is written into the measurement JSON.

## License

MIT. See [LICENSE](LICENSE).