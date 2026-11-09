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

## License

MIT. See [LICENSE](LICENSE).