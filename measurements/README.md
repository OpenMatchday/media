# Measurement log (started OM-013)

Baseline media gates G1–G7 are recorded here as dated JSON under `results/`.

| Gate | Meaning | Status |
|---|---|---|
| G1 Capture + hardware encode | Stable frames; encoder path identified (D4) | started (OM-013); two-path continued (OM-014) |
| G2 Transport glass-to-glass | Latency/loss on consumer router (D3) | started single-host SRT only (OM-013/014); dual-device consumer-router pending |
| G3 Cut timing | Cut/Take Program switch | started (OM-015); identity-switch latency recorded in `results/g3_cut_take_*.json` |
| G4 Compositing | Graphics over video | not started |
| G5 A/V sync and drift | Sync tolerance | not started |
| G6 Recording | File playback | not started |
| G7 Multiple outputs | Sustained combinations | not started |

Every result file should include device/OS, network medium, transport, codec, commit SHA when known, and measured values only (never invented targets).

Do not publish these numbers on the website or in docs badges until OM-047 campaign review.
