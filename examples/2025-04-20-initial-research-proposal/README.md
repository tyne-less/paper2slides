# Historical baseline snapshot

This directory is a checked-in snapshot of an early public baseline run. It remains useful for inspecting the default pipeline contract, but it is not a statement of the current research evaluation status.

The original source PDF is not distributed in the public repository, but the generated artifacts are kept here so the current pipeline contract can be inspected without rerunning the system.

Included artifacts:
- `01_raw_text.txt`
- `02_pages.json`
- `03_blocks.json`
- `04_structured_document.json`
- `05_slide_deck.json`
- `06_slides.tex`
- `RUN_SUMMARY.md`

Purpose:
- let readers inspect the baseline intermediate representations without running the code,
- make the pipeline contract visible from the repository alone,
- provide a stable, redistributable example while the main system remains under active development.

Note:
- labels such as `placeholder_ocr` and `Minimal placeholder pipeline output` are preserved inside the historical generated artifacts; they describe that early baseline, not the current project positioning,
- the snapshot contains no source PDF, local path, or full evaluation-paper transcription,
- current aggregate results and research methods are documented in the repository root and `docs/research/`.
