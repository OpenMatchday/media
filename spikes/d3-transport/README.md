# D3 media transport spike (OM-004)

Harness for latency/loss probes. Full write-up: sibling `docs` repo `spikes/d3-media-transport.md`.

## Commands

```text
python measure_udp_rtp.py --self-check
python measure_udp_rtp.py --bind 127.0.0.1 --dest 127.0.0.1 --out results/udp-rtp-loopback.json
cargo run --release --manifest-path srt_probe/Cargo.toml -- --self-check
cargo run --release --manifest-path srt_probe/Cargo.toml -- --latency-ms 120 --out results/srt-loopback.json
python measure_ffmpeg_srt_smoke.py --out results/ffmpeg-srt-smoke.json
```

Do not publish these numbers as product targets. They are transport-only, shared-clock, single-host.
