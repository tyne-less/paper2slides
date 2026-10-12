from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pdf2slides.application_rendering import _draw_body, render_application_slide
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

    def test_reflowed_multiline_bullet_has_one_marker(self) -> None:
        image = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=18)
        layout = self._body_layout("reflow_within_fixed_box")
        slide = Slide("slide_01", "Title", ["This bullet is deliberately long enough to wrap over more than one line."], ["Synthetic"], "text")
        _draw_body(draw, slide, layout, font)
        marker_pixels = sum(
            image.getpixel((x, y)) == (17, 17, 17)
            for x in range(layout.body_box.x, layout.body_box.x + 8)
            for y in range(layout.body_box.y, layout.body_box.y + layout.body_box.height)
        )
        self.assertGreater(marker_pixels, 0)
        self.assertLess(marker_pixels, 70)

    def test_unchanged_body_rejects_a_bullet_that_requires_reflow(self) -> None:
        image = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=18)
        layout = self._body_layout("unchanged")
        slide = Slide("slide_01", "Title", ["This bullet is deliberately long enough to wrap over more than one line."], ["Synthetic"], "text")
        with self.assertRaises(ValueError):
            _draw_body(draw, slide, layout, font)

    def _body_layout(self, body_behavior: str) -> ApplicationLayout:
        evidence = EvidenceAsset(
            repository_path="fixtures/evidence.png",
            sha256="0" * 64,
            intrinsic_width=1,
            intrinsic_height=1,
            crop=PixelBox(0, 0, 1, 1),
        )
        return ApplicationLayout(PixelCanvas(240, 240), PixelBox(0, 0, 1, 1), PixelBox(10, 10, 180, 220), PixelBox(190, 10, 40, 40), evidence, body_behavior=body_behavior)


if __name__ == "__main__":
    unittest.main()
