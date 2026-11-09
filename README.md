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

## License

MIT. See [LICENSE](LICENSE).