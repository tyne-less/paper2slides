from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .schema import ApplicationLayout, PixelBox, Slide


RENDERER_ID = "pdf2slides-pillow-layout-v1"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines = [words[0]]
    for word in words[1:]:
        candidate = f"{lines[-1]} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def _fit_image(image: Image.Image, width: int, height: int, mode: str) -> Image.Image:
    source = image.convert("RGB")
    if mode == "stretch":
        return source.resize((width, height), Image.Resampling.LANCZOS)
    ratio = min(width / source.width, height / source.height) if mode == "contain" else max(width / source.width, height / source.height)
    size = (max(1, round(source.width * ratio)), max(1, round(source.height * ratio)))
    resized = source.resize(size, Image.Resampling.LANCZOS)
    if mode == "contain":
        result = Image.new("RGB", (width, height), "white")
        result.paste(resized, ((width - size[0]) // 2, (height - size[1]) // 2))
        return result
    left = max(0, (size[0] - width) // 2)
    top = max(0, (size[1] - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _draw_text(draw: ImageDraw.ImageDraw, lines: list[str], box: PixelBox, font: ImageFont.ImageFont, bullet: bool = False) -> None:
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + 6
    x = box.x + (18 if bullet else 0)
    y = box.y
    for line in lines:
        if y + line_height > box.y + box.height:
            raise ValueError("Text exceeds its layout box")
        if bullet:
            draw.ellipse((box.x, y + 5, box.x + 7, y + 12), fill="#111111")
        draw.text((x, y), line, font=font, fill="#111111")
        y += line_height


def _load_evidence(layout: ApplicationLayout, repository_root: Path) -> Image.Image:
    root = repository_root.resolve()
    path = (root / layout.evidence.repository_path).resolve()
    if root not in path.parents or not path.is_file():
        raise ValueError("Evidence path is not a repository file")
    if hashlib.sha256(path.read_bytes()).hexdigest() != layout.evidence.sha256:
        raise ValueError("Evidence asset differs from its recorded SHA-256")
    with Image.open(path) as image:
        if image.size != (layout.evidence.intrinsic_width, layout.evidence.intrinsic_height):
            raise ValueError("Evidence dimensions differ from its layout record")
        crop = layout.evidence.crop
        return image.crop((crop.x, crop.y, crop.x + crop.width, crop.y + crop.height)).copy()


def render_application_slide(slide: Slide, repository_root: Path) -> bytes:
    layout = slide.application_layout
    if layout is None:
        raise ValueError("Slide has no application layout")
    image = Image.new("RGB", (layout.canvas.width, layout.canvas.height), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=28)
    body_font = ImageFont.load_default(size=18)
    _draw_text(draw, _wrap(draw, slide.title, title_font, layout.title_box.width), layout.title_box, title_font)
    body_lines: list[str] = []
    for bullet in slide.bullets:
        body_lines.extend(_wrap(draw, bullet, body_font, layout.body_box.width - 18))
    _draw_text(draw, body_lines, layout.body_box, body_font, bullet=True)
    evidence = _load_evidence(layout, repository_root)
    image.paste(_fit_image(evidence, layout.evidence_box.width, layout.evidence_box.height, layout.evidence.fit), (layout.evidence_box.x, layout.evidence_box.y))
    output = io.BytesIO()
    image.save(output, format="PNG", compress_level=9, optimize=False)
    return output.getvalue()
