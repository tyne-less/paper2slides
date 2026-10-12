# Source grouping

Source grouping connects reconstructed text or captions with the visual or structural evidence that may support a slide. The research question is not only whether a group has many matching tokens, but whether it has the right identity and boundaries.

## Established lesson

Token coverage alone is insufficient. A group may have good token overlap while including neighboring content, pointing to the wrong object, or reflecting an incomplete source export. Combining coverage with identity, anchor, and boundary diagnostics distinguishes clean groups from these failure modes.

## Boundary repair as a reviewed proposal

A bounded review of four boundary-over-inclusion cases tested trim proposals on copied artifacts. Across those reviewed cases, boundary precision increased from `0.500000` to `1.000000`, while token coverage increased from `0.333333` to `0.500000`. This supports a narrow conclusion: after review, explicit trims can repair boundary over-inclusion in the evaluated copies.

It does not establish automatic repair safety, production-path correctness, generalization beyond the reviewed cases, or solutions for identity and incomplete-export failures. The current public baseline therefore leaves source-grouping behavior unchanged.
