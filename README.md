# Paper2Slides

> An evaluation-driven multimodal research prototype for transforming academic PDFs into structured presentation outputs.

Paper2Slides studies academic PDF-to-slides generation as a structured document-understanding problem, not simply text summarization. A useful presentation needs recoverable document structure, readable order, grounded visual evidence, and an explicit slide plan before it can be rendered.

```text
academic PDF -> structured document representation -> slide plan -> presentation output
```

## What runs by default

The public command-line pipeline is a compact, reproducible baseline. It extracts text with `pdftotext`, builds page and block records, applies lightweight structure and reading-order heuristics, writes a structured document JSON file, plans a slide deck, and exports LaTeX Beamer source.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .

# Install Poppler separately so `pdftotext` is available on PATH.
python3 -m pdf2slides path/to/local-paper.pdf --output-root outputs
```

On macOS, install Poppler with `brew install poppler`; on Ubuntu or Debian, use `sudo apt-get install poppler-utils`.

The baseline produces explicit intermediate artifacts:

```text
outputs/<pdf-stem>/
  01_raw_text.txt
  02_pages.json
  03_blocks.json
  04_structured_document.json
  05_slide_deck.json
  06_slides.tex
  RUN_METADATA.json
  RUN_SUMMARY.md
```

## Research modules

The repository also includes reusable components developed alongside the baseline. They are deliberately separate from the default CLI while their integration is evaluated:

- `page_regions.py` converts YOLO-format page labels into page-coordinate region records.
- `schema.py` defines an optional slide application-layout contract for a title, body, and visual-evidence region.
- `application_rendering.py` deterministically renders an application-layout slide PNG from a checked evidence asset.
- `application_variants.py` creates isolated layout variants without changing the source deck.

These modules support controlled experiments in layout, crop/fit behavior, and visual evidence selection. They do not imply that the default baseline already performs learned layout prediction or production-quality presentation design.

## Research progress

### Document reconstruction and reading order

On a frozen eight-paper aggregate benchmark, high-level structure recovery is stable: title `8/8`, abstract `8/8`, normalized sections `71/71`, and subsections `91/91`. Fine-grained reconstruction remains harder: paragraph anchors `340/355`, paragraph order `291/340`, figure captions `22/23`, figure-caption order `20/22`, and table captions `35/36`.

These are diagnostic aggregate results, not a claim that every document object is fully reconstructed. They show that paragraph boundaries, local reading order, captions, formulas, tables, and footnotes remain central research problems. See [the reconstruction note](docs/research/reconstruction-evaluation.md) and [the reading-order evaluation guide](docs/reading-order-evaluation.md).

### Human-authored structural evaluation

The project has progressed from proxy-only reading-order scores to parser-independent, human-authored fixtures. Fixtures can represent paragraphs, formulas, table bodies, footnotes, and page or column structure when available. Parser candidates are reviewed against these references rather than treated as ground truth. [The fixture-evaluation note](docs/research/human-fixture-evaluation.md) describes the public methodology and its boundaries.

### Downstream slide generation

Research prototypes cover slide planning, visual-evidence selection, layout variants, deterministic rendering, crop/fit behavior, human visual judgment, and downstream slide-impact analysis. These experiments test whether reconstructed evidence improves a slide, rather than assuming that successful extraction is sufficient.

### Source grouping

Source grouping research found that token coverage alone cannot establish whether a group is clean. Combined diagnostics can distinguish clean groups from boundary, identity, and incomplete-export failures; reviewed trim proposals provide a practical repair mechanism for boundary over-inclusion. [The source-grouping note](docs/research/source-grouping.md) records the supported conclusion and its limits.

## Demo snapshot

The checked-in [initial research proposal snapshot](examples/2025-04-20-initial-research-proposal/README.md) is a small, inspectable example of the default pipeline. It includes raw extraction, page and block representations, a structured-document IR, a slide plan, and Beamer source. The original PDF is intentionally not redistributed.

## Scope and limitations

Paper2Slides is a research prototype with an end-to-end scaffold and increasingly rigorous evaluation. The default pipeline still uses lightweight extraction, parsing, and slide-planning heuristics. Experimental modules are not silently substituted into that path. The work does not yet claim robust performance across arbitrary publication styles or a completed automatic paper-to-slides system.

## Development

Run the tests with:

```bash
python3 -m unittest discover -s tests
```

The repository contains no source PDFs or full transcriptions from the evaluation corpus. Use local papers for new baseline runs and consult the research notes for aggregate, reproducible conclusions.
