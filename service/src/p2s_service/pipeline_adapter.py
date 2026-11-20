"""Runs the existing pdf2slides stages one by one so each stage can be timed.

The stage order mirrors pdf2slides.pipeline.run_pipeline; tests/test_pipeline_adapter.py
checks that the slide deck and Beamer output stay identical to run_pipeline.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pdf2slides.stages import (
    detect_layout_blocks,
    extract_text_from_pdf,
    generate_slide_deck,
    parse_structure,
    render_beamer,
    run_pdftotext,
    split_into_pages,
    transcribe_text_layer_blocks,
    write_json,
)

PIPELINE_VERSION = "pdf2slides-rules-v1"
PDFTOTEXT_MODES = ("default", "layout", "columns", "hybrid")

OUTPUT_FILES = {
    "raw_text": "01_raw_text.txt",
    "pages": "02_pages.json",
    "blocks": "03_blocks.json",
    "structured_document": "04_structured_document.json",
    "slide_deck": "05_slide_deck.json",
    "beamer": "06_slides.tex",
}

STAGE_NAMES = (
    "extract_text",
    "split_pages",
    "detect_layout",
    "transcribe",
    "parse_structure",
    "generate_slides",
    "render_beamer",
)

T = TypeVar("T")


@dataclass(frozen=True)
class StageTiming:
    stage: str
    started_at: datetime
    duration_ms: int
    status: str
    error_type: str | None = None


class StageError(Exception):
    def __init__(self, stage: str):
        super().__init__(f"stage {stage} failed")
        self.stage = stage


def _timed(stage: str, timings: list[StageTiming], fn: Callable[[], T]) -> T:
    started_at = datetime.now(UTC)
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000)
        timings.append(StageTiming(stage, started_at, duration_ms, "failed", type(exc).__name__))
        raise StageError(stage) from exc
    duration_ms = round((time.perf_counter() - start) * 1000)
    timings.append(StageTiming(stage, started_at, duration_ms, "succeeded"))
    return result


def run_stages(
    input_pdf: Path, run_dir: Path, pdftotext_mode: str, timings: list[StageTiming]
) -> dict[str, Path]:
    """Run all stages, appending one StageTiming per stage (including a failed one) to timings."""
    run_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: run_dir / filename for name, filename in OUTPUT_FILES.items()}

    def extract():
        if pdftotext_mode == "hybrid":
            raw = extract_text_from_pdf(input_pdf, paths["raw_text"], pdftotext_mode="default")
            columns = split_into_pages(run_pdftotext(input_pdf, pdftotext_mode="columns"))
            return raw, columns
        raw = extract_text_from_pdf(input_pdf, paths["raw_text"], pdftotext_mode=pdftotext_mode)
        return raw, None

    raw_text, column_pages = _timed("extract_text", timings, extract)

    def split():
        pages = split_into_pages(raw_text)
        write_json(paths["pages"], [page.to_dict() for page in pages])
        return pages

    pages = _timed("split_pages", timings, split)
    pages = _timed(
        "detect_layout",
        timings,
        lambda: detect_layout_blocks(
            pages, column_pages=column_pages, hybrid_body_order=pdftotext_mode == "hybrid"
        ),
    )

    def transcribe():
        transcribed = transcribe_text_layer_blocks(pages)
        write_json(paths["blocks"], [page.to_dict() for page in transcribed])
        return transcribed

    pages = _timed("transcribe", timings, transcribe)

    def parse():
        document = parse_structure(input_pdf, pages)
        write_json(paths["structured_document"], document.to_dict())
        return document

    document = _timed("parse_structure", timings, parse)

    def deck():
        slide_deck = generate_slide_deck(document, run_dir)
        write_json(paths["slide_deck"], slide_deck.to_dict())
        return slide_deck

    slide_deck = _timed("generate_slides", timings, deck)
    _timed("render_beamer", timings, lambda: render_beamer(slide_deck, paths["beamer"]))
    return paths
