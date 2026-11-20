# Human-fixture evaluation

## Motivation

Parser output is useful evidence, but it cannot be the gold standard for evaluating the parser. Earlier proxy scores used normalized anchors and sequences to reveal downstream reading-order problems. Human-authored fixtures provide an independent representation of the document objects that should be recovered.

## Fixture design

A fixture can describe paragraphs, formulas, table bodies, footnotes, and page or column structure when the source requires it. Each object is recorded from the source document rather than inferred from parser output. This enables evaluation when one parser block corresponds to several real objects, one object is fragmented across blocks, or a boundary falls inside a parser block.

## Reviewed correspondence

Parser candidates are aligned to fixture objects through review. Correspondence can be one-to-one, many-to-one, one-to-many, or split-required. The review distinguishes structural matching from a parser’s own identifiers and does not silently promote a candidate record to gold.

## Current boundary

This methodology is being extended across papers. Public documentation intentionally describes the method and its evaluation readiness without publishing raw annotations, reviewer records, private workflow metadata, or results from fixtures whose parser correspondence is not finalized.
