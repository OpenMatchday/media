# One camera to director (OM-013)

Capture one camera (or lavfi `testsrc`), encode (hardware preferred), send over **SRT**, receive on the director side as a Preview-path snapshot.

Media plane mediums: **wifi** / **ethernet** only (`gates/media_medium.py`).

## Commands

```text
python preview/run_one_camera.py --self-check
python preview/run_one_camera.py --source camera --medium wifi --out measurements/results/om013-camera-wifi.json
python preview/run_one_camera.py --source camera --medium wifi --host 10.200.78.185 --out measurements/results/om013-camera-wifi-hairpin.json
```

Self-check uses `testsrc` + `libx264` when needed so CI hosts without a camera still pass.

Director Preview JPEG (when ok): `preview/out/director_preview.jpg`.

Measurement JSON lands under `measurements/results/`. Numbers are single-host capture/encode/SRT receive records, not published product targets.
