# Research materials

日本語の概要は [README.ja.md](../README.ja.md#研究) にあります。

Paper2Slides studies academic PDF-to-slides generation as a structured document-understanding
problem, not simply text summarization. A useful presentation needs recoverable document
structure, readable order, grounded visual evidence, and an explicit slide plan before it can be
rendered. The pipeline code is in [`../src/pdf2slides`](../src/pdf2slides); this directory holds
the evaluation protocols, research notes and experiment records.

| Path | Contents |
|---|---|
| [`docs/`](docs) | Research notes: [reconstruction evaluation](docs/reconstruction-evaluation.md), [human-authored fixture evaluation](docs/human-fixture-evaluation.md), [source grouping](docs/source-grouping.md), and the [reading-order evaluation protocol](docs/reading-order-evaluation.md) |
| [`experiments/`](experiments) | Dated records of individual heuristic experiments (April 2026) |
| [`eval-sets/`](eval-sets) | Definition of the Findings ACL 2024 evaluation set and its gold fields |
| [`examples/`](examples) | A complete pipeline output for one document, with every intermediate artifact |
| [`scripts/`](scripts) | `eval_reading_order.py`, the reading-order evaluation script |

The experiment records were written when these directories sat at the repository root, so the
paths inside them (for example `scripts/eval_reading_order.py` or `docs/reading-order-evaluation.md`)
are relative to the old layout; prefix them with `research/`. The records themselves are kept
unchanged.

## Research modules

The pipeline package also includes reusable components developed alongside the baseline. They
are deliberately separate from the default CLI while their integration is evaluated:

- `page_regions.py` converts YOLO-format page labels into page-coordinate region records.
- `schema.py` defines an optional slide application-layout contract for a title, body, and visual-evidence region.
- `application_rendering.py` deterministically renders an application-layout slide PNG from a checked evidence asset.
- `application_variants.py` creates isolated layout variants without changing the source deck.

These modules support controlled experiments in layout, crop/fit behavior, and visual evidence
selection. They do not imply that the default baseline already performs learned layout
prediction or production-quality presentation design.

## Research progress

### Document reconstruction and reading order

On a frozen eight-paper aggregate benchmark, high-level structure recovery is stable: title
`8/8`, abstract `8/8`, normalized sections `71/71`, and subsections `91/91`. Fine-grained
reconstruction remains harder: paragraph anchors `340/355`, paragraph order `291/340`, figure
captions `22/23`, figure-caption order `20/22`, and table captions `35/36`.

These are diagnostic aggregate results, not a claim that every document object is fully
reconstructed. They show that paragraph boundaries, local reading order, captions, formulas,
tables, and footnotes remain central research problems. See
[the reconstruction note](docs/reconstruction-evaluation.md) and
[the reading-order evaluation guide](docs/reading-order-evaluation.md).

### Human-authored structural evaluation

The project has progressed from proxy-only reading-order scores to parser-independent,
human-authored fixtures. Fixtures can represent paragraphs, formulas, table bodies, footnotes,
and page or column structure when available. Parser candidates are reviewed against these
references rather than treated as ground truth.
[The fixture-evaluation note](docs/human-fixture-evaluation.md) describes the public methodology
and its boundaries.

### Downstream slide generation

Research prototypes cover slide planning, visual-evidence selection, layout variants,
deterministic rendering, crop/fit behavior, human visual judgment, and downstream slide-impact
analysis. These experiments test whether reconstructed evidence improves a slide, rather than
assuming that successful extraction is sufficient.

### Source grouping

Source grouping research found that token coverage alone cannot establish whether a group is
clean. Combined diagnostics can distinguish clean groups from boundary, identity, and
incomplete-export failures; reviewed trim proposals provide a practical repair mechanism for
boundary over-inclusion. [The source-grouping note](docs/source-grouping.md) records the
supported conclusion and its limits.

## Demo snapshot

The checked-in [initial research proposal snapshot](examples/2025-04-20-initial-research-proposal/README.md)
is a small, inspectable example of the default pipeline. It includes raw extraction, page and
block representations, a structured-document IR, a slide plan, and Beamer source. The original
PDF is intentionally not redistributed.

## Scope and limitations

Paper2Slides is a research prototype with an end-to-end scaffold and increasingly rigorous
evaluation. The default pipeline still uses lightweight extraction, parsing, and slide-planning
heuristics. Experimental modules are not silently substituted into that path. The work does not
yet claim robust performance across arbitrary publication styles or a completed automatic
paper-to-slides system.

The repository contains no source PDFs or full transcriptions from the evaluation corpus. Use
local papers for new baseline runs and consult the research notes for aggregate, reproducible
conclusions.
