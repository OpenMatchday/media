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

## Notes (OM-015 / Cut/Take + G3)

- Explicit Cut/Take switches Program to the current Preview source; no silent auto-switch (AC-2.2).
- Harness: `preview/run_cut_take.py` (`CUT_TAKE_OK` via `--self-check`); gate `gates/cut_take.py`.
- G3 records measured `cut_latency_ms` for Program identity apply under `measurements/results/`.
- Tally on the Program camera is a control-plane side effect (AC-2.3); UI lives in sibling `app`.

## Notes (OM-016 / A/V sync + G5)

- Sync monitor measures audio-vs-video `start_time` offset after a known lavfi `itsoffset` inject (AC-2.6).
- Drift correction remuxes with inverse `itsoffset`; residual recorded per sample over a short rehearsal.
- Harness: `preview/run_av_sync.py` (`AV_SYNC_OK` via `--self-check`); gate `gates/av_sync.py`.
- Results under `measurements/results/g5_av_sync_*.json`. No unpublished product sync target.

## Notes (OM-017 / connection health + source-loss fallback)

- Per-source health states `connected` / `degraded` / `lost` with timestamps (AC-2.8; protocol HealthState).
- On Program source drop, configured safe state applies: `last_frame_freeze`, `slate`, or `alternate_source` without ending the session (AC-2.7).
- Harness: `preview/run_source_health.py` (`SOURCE_HEALTH_OK` via `--self-check`); gate `gates/source_health.py`.
- Results under `measurements/results/om017_source_health_*.json`.

## Notes (OM-026 / output combination enablement + G7)

- Live aspects `16:9`, `9:16`, `1:1` plus recording; release enablement requires measured aspect flags and combination sustain (AC-3.1 / AC-3.11).
- Harness: `preview/run_output_combination.py` (`OUTPUT_COMBINATION_OK` via `--self-check`); gate `gates/output_combination.py`.
- Results under `measurements/results/om026_output_combination_*.json`. Support flags stay unverified until OM-047; harness sustain does not auto-enable release.
- App-side gates live in sibling `app` (`CapabilityMatrix` + Director Outputs panel).

## Notes (OM-032 / recording + G6)

- Explicit start/stop of Program recording; completed file probes for video+audio and decodes for playback (G6 / AC-3.1 recording path).
- Crash mid-record leaves recording stopped; never auto-restarts (AC-5.6 with OM-019).
- Harness: `preview/run_recording.py` (`RECORDING_OK` via `--self-check`); gate `gates/recording.py`.
- Results under `measurements/results/om032_recording_*.json`. Host-tool encode uses libx264/aac for measurement; product path prefers D4 hardware.

## Notes (OM-030 / four-corner calibration)

- Freeze synthetic board frame, validate TL/TR/BR/BL (order, convexity, min area), compute 4-point homography (OpenCV getPerspectiveTransform form, h33=1), write rectified preview, define OCR regions with sport field allowlists.
- Geometry: `calibration/geometry.py` + `calibration/regions.py`. Harness: `preview/run_calibration.py` (`CALIBRATION_OK`); gate `gates/calibration.py`.
- Regions omit fields absent for a sport variant (e.g. no `shot_clock` for basketball/youth or futsal). Normalized corners reproject across scale/rotation (AC-4.6).
- UI + Dart geometry mirror live in sibling `app` (`lib/calibration/`).

## Notes (OM-031 / OCR regions + camera-move)

- Region OCR readings carry value, calibrated confidence, timestamp, staleness (AC-5.2). Confidence prefers Tesseract TSV conf/100 when >0, else digit-parse quality (D5 TSV-zero caveat).
- Camera-move detect: corner-patch fingerprint MAD vs freeze reference; threshold = midpoint of measured same-frame and shifted-frame MAD. Move marks readings + live crops stale and requires recalibration (AC-4.7).
- Modules: `ocr/`. Harness: `preview/run_ocr_regions.py` (`OCR_REGIONS_OK`); gate `gates/ocr_regions.py`.
- App authority gate remains `minConfidence=0.7` against the calibrated signal.

## License

MIT. See [LICENSE](LICENSE).