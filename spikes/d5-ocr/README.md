# D5 OCR engine spike (OM-006)

On-device OCR probes for scoreboard reader engine choice (AC-4.*, AC-5.2).
Full write-up: sibling `docs` repo `spikes/d5-ocr-engine.md`.

## Commands

```text
python make_fixtures.py
python inventory_ocr.py
python run_ocr.py --out results/windows-tesseract-fixtures.json
python run_ocr.py --self-check
```

Synthetic PNG fixtures land under `fixtures/` (committed). Tesseract stdout/tsv under `out/` (gitignored). JSON under `results/` is committed.
Optional: `python fetch_tessdata_fast.py` downloads Apache-2.0 `eng.traineddata` from tessdata_fast for size comparison (cached under `tessdata_cache/`, gitignored).
