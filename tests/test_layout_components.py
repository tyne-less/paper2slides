from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pdf2slides.application_rendering import render_application_slide
from pdf2slides.application_variants import create_application_variant, write_application_variant
from pdf2slides.page_regions import parse_yolo_label_line
from pdf2slides.schema import ApplicationLayout, EvidenceAsset, PixelBox, PixelCanvas, Slide, SlideDeck


class LayoutComponentTests(unittest.TestCase):
    def test_yolo_region_is_converted_to_page_coordinates(self) -> None:
        region = parse_yolo_label_line("5 0.5 0.25 0.2 0.1 0.9", 2, 3, 1000, 2000)
        self.assertEqual(region.region_id, "p2_r3")
        self.assertEqual(region.region_type, "figure")
        self.assertEqual(region.bbox, [400.0, 400.0, 600.0, 600.0])
        self.assertEqual(region.confidence, 0.9)

    def test_layout_variant_renders_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            evidence_path = fixtures / "evidence.png"
            Image.new("RGB", (40, 30), "blue").save(evidence_path)
            evidence = EvidenceAsset(
                repository_path="fixtures/evidence.png",
                sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                intrinsic_width=40,
                intrinsic_height=30,
                crop=PixelBox(0, 0, 40, 30),
                fit="contain",
            )
            layout = ApplicationLayout(PixelCanvas(400, 240), PixelBox(20, 10, 360, 50), PixelBox(20, 80, 160, 140), PixelBox(220, 80, 160, 140), evidence)
            deck = SlideDeck("synthetic.pdf", "outputs", [Slide("slide_01", "Synthetic title", ["Synthetic body text"], ["Synthetic"], "text")])
            variant = create_application_variant(deck, "slide_01", layout, root / "variants")
            first = render_application_slide(variant.deck.slides[0], root)
            second = render_application_slide(variant.deck.slides[0], root)
            self.assertEqual(first, second)
            self.assertEqual(Image.open(io.BytesIO(first)).size, (400, 240))
            manifest = write_application_variant(variant, root)
            self.assertTrue((variant.directory / manifest["rendered_slide"]).is_file())
            self.assertEqual(deck.slides[0].application_layout, None)


if __name__ == "__main__":
    unittest.main()
