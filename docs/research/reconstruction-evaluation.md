# Reconstruction evaluation

## Benchmark snapshot

The current public reconstruction snapshot is a frozen aggregate across eight scientific papers. It is intended to expose the project’s diagnostic baseline, not to represent a universal benchmark result.

| Object or relation | Result |
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

## Interpretation

Titles, abstracts, and section hierarchy are relatively stable under the current baseline. The remaining misses concentrate in finer-grained reconstruction: paragraph segmentation and local ordering, caption recovery, and document regions whose surface form is affected by extraction and layout.

The paragraph results should not be read as pure reading-order accuracy. Anchor recall also reflects text extraction, OCR-like surface variation, normalization, and block segmentation. Likewise, caption results are affected by label detection and grouping. The value of the scorecard is that it makes these downstream-sensitive failures visible and comparable across controlled changes.

## Evaluation boundary

The public repository reports aggregates and methodology only. It does not redistribute evaluation PDFs, full article transcriptions, raw review records, or private annotation workflows. Strict, parser-independent fixture evaluation is being expanded to additional papers; no performance result is reported here for work still under validation.
