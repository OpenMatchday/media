# Preview harness

## One camera to director (OM-013)

Capture one camera (or lavfi `testsrc`), encode (hardware preferred), send over **SRT**, receive on the director side as a Preview-path snapshot.

```text
python preview/run_one_camera.py --self-check
python preview/run_one_camera.py --source camera --medium wifi --out measurements/results/om013-camera-wifi.json
```

Expect `ONE_CAMERA_PREVIEW_OK` for self-check. Snapshot: `preview/out/director_preview.jpg`.

## Two cameras + independent audio (OM-014)

Two concurrent Preview paths (cam-1, cam-2) plus a selectable audio source with peak/RMS meters. Audio is not forced to follow the video Program source (AC-2.4 / AC-2.5).

```text
python preview/run_two_cameras_audio.py --self-check
python preview/run_two_cameras_audio.py --source camera --audio-source device --medium wifi --out measurements/results/om014-camera-wifi.json
python preview/run_two_cameras_audio.py --source camera --audio-source device --medium wifi --host <wifi-ip> --out measurements/results/om014-camera-wifi-hairpin.json
```

Self-check uses two `testsrc` paths + lavfi `sine` so CI hosts without cameras still pass (`TWO_CAMERAS_AUDIO_OK`).

Snapshots: `preview/out/director_preview_cam1.jpg`, `preview/out/director_preview_cam2.jpg`.

When only one usable dshow video device is present, cam-2 uses lavfi `testsrc` and the JSON records that fallback.

Media plane mediums: **wifi** / **ethernet** only (`gates/media_medium.py`).

## Cut/Take G3 (OM-015)

```text
python preview/run_cut_take.py --self-check
python preview/run_cut_take.py --action take --with-srt
```

Expect `CUT_TAKE_OK`. Results: `measurements/results/g3_cut_take_*.json`.

## A/V sync + drift G5 (OM-016)

```text
python preview/run_av_sync.py --self-check
python preview/run_av_sync.py --offset-ms 80 --drift-per-sample-ms 5 --samples 5
```

Expect `AV_SYNC_OK`. Results: `measurements/results/g5_av_sync_*.json`.

## Connection health + source-loss fallback (OM-017)

```text
python preview/run_source_health.py --self-check
python preview/run_source_health.py --fallback alternate_source
```

Expect `SOURCE_HEALTH_OK`. Results: `measurements/results/om017_source_health_*.json`.
