# D4 codecs / hardware encoders spike (OM-005)

Encode capability probes for G1. Full write-up: sibling `docs` repo `spikes/d4-codecs-encoders.md`.

## Commands

```text
python inventory_host.py
python probe_encoders.py --out results/windows-encode-smokes.json
wsl -e bash spikes/d4-codecs/wsl_vaapi_note.sh
```

Raw YUV and MP4 intermediates land in `out/` (gitignored). JSON under `results/` is committed.
