import json

import pytest
from pdf2slides.pipeline import run_pipeline

from conftest import make_pdf
from p2s_service.pipeline_adapter import OUTPUT_FILES, StageTiming, run_stages


@pytest.mark.parametrize("mode", ["default", "hybrid"])
def test_adapter_output_matches_run_pipeline(tmp_path, mode):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(make_pdf())

    reference = run_pipeline(pdf, tmp_path / "reference", pdftotext_mode=mode)
    timings: list[StageTiming] = []
    outputs = run_stages(pdf, tmp_path / "adapter", mode, timings)

    assert reference.latex_path.read_text() == outputs["beamer"].read_text()
    reference_deck = json.loads(reference.slide_deck_path.read_text())
    adapter_deck = json.loads(outputs["slide_deck"].read_text())
    reference_deck.pop("output_dir")
    adapter_deck.pop("output_dir")
    assert reference_deck == adapter_deck
    for name in ("pages", "blocks", "structured_document"):
        assert (reference.run_dir / OUTPUT_FILES[name]).read_text() == outputs[name].read_text()


def test_adapter_records_one_timing_per_stage(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(make_pdf())
    timings: list[StageTiming] = []
    run_stages(pdf, tmp_path / "run", "default", timings)
    assert [t.stage for t in timings] == [
        "extract_text",
        "split_pages",
        "detect_layout",
        "transcribe",
        "parse_structure",
        "generate_slides",
        "render_beamer",
    ]
    assert all(t.status == "succeeded" and t.duration_ms >= 0 for t in timings)
