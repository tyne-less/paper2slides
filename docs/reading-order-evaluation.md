# Reading-order evaluation

Reading order is evaluated through its effects on the structured document representation: paragraph sequence, caption sequence, section continuity, and subsection continuity. It should not be interpreted as an isolated text-extraction score.

## Evolution of the methodology

The evaluation approach has evolved in four steps:

1. Proxy evaluation compared normalized paragraph anchors and captions with annotated reference sequences.
2. Reconstruction diagnostics separated heading, paragraph, caption, and ordering failures so that a single score did not conceal different error types.
3. Human-authored strict fixtures introduced parser-independent document objects, including paragraphs and other structural regions where annotated.
4. Reviewed parser-to-fixture correspondence makes strict scoring possible without assuming that parser blocks are gold units.

The current public baseline metrics remain useful aggregate diagnostics. Strict fixture evaluation is a complementary methodology, not a replacement claim for the default CLI.

## Aggregate reconstruction diagnostics

The frozen eight-paper aggregate reports:

| Measure | Result |
| --- | ---: |
| Title | 8/8 |
| Abstract | 8/8 |
| Normalized sections | 71/71 |
| Subsections | 91/91 |
| Paragraph anchors | 340/355 |
| Paragraph order | 291/340 |
| Figure captions | 22/23 |
| Figure-caption order | 20/22 |
| Table captions | 35/36 |

Paragraph-anchor matching detects whether an intended paragraph surface can be found after normalization. Paragraph order measures whether matched anchors appear in the intended sequence. Caption measures apply the same principle to figure and table captions. These measures mix extraction quality, grouping, OCR surface variation, and reading order; they are diagnostic proxies rather than direct block-order supervision.

## What strict fixtures add

Human-authored fixtures can express the document units that a parser should recover, independently of the parser's own segmentation. They support review of one-to-one, many-to-one, and split-required correspondence. This matters because a parser block can span multiple real paragraphs, which a one-block-one-unit metric would otherwise hide.

See [human-fixture evaluation](research/human-fixture-evaluation.md) for the methodology and [reconstruction evaluation](research/reconstruction-evaluation.md) for benchmark interpretation.
