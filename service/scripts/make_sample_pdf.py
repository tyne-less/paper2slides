"""Write a small two-page paper-like PDF for the smoke test: python make_sample_pdf.py out.pdf"""

import sys

import pymupdf

doc = pymupdf.open()
pages = [
    [
        "Robust Parsing of Scholarly Documents",
        "Abstract",
        "We study how parsing errors propagate to generated slides.",
        "Our pipeline recovers reading order and section structure.",
        "1 Introduction",
        "Academic PDFs mix columns, figures and formulas.",
        "Errors in early stages affect every downstream stage.",
    ],
    ["2 Method", "We time each stage of the pipeline separately."],
]
for lines in pages:
    page = doc.new_page()
    for i, line in enumerate(lines):
        page.insert_text((72, 72 + i * 24), line, fontsize=11)
doc.save(sys.argv[1])
