from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .application_rendering import RENDERER_ID, render_application_slide
from .schema import ApplicationLayout, Slide, SlideDeck


def canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApplicationVariant:
    variant_id: str
    directory: Path
    deck: SlideDeck
    target_slide_id: str


def create_application_variant(deck: SlideDeck, target_slide_id: str, layout: ApplicationLayout, output_root: Path) -> ApplicationVariant:
    slides = list(deck.slides)
    index = next((index for index, slide in enumerate(slides) if slide.slide_id == target_slide_id), None)
    if index is None:
        raise ValueError("Target slide was not found")
    variant_id = f"application-{canonical_fingerprint({'deck': deck.to_dict(), 'slide': target_slide_id, 'layout': layout.to_dict()})[:16]}"
    slides[index] = Slide(
        slide_id=slides[index].slide_id,
        title=slides[index].title,
        bullets=list(slides[index].bullets),
        source_sections=list(slides[index].source_sections),
        layout_hint=slides[index].layout_hint,
        application_layout=layout,
    )
    return ApplicationVariant(variant_id, output_root / variant_id, SlideDeck(deck.source_pdf, str(output_root / variant_id), slides), target_slide_id)


def write_application_variant(variant: ApplicationVariant, repository_root: Path) -> dict[str, str]:
    variant.directory.mkdir(parents=True, exist_ok=True)
    deck_path = variant.directory / "slide_deck.json"
    render_path = variant.directory / f"{variant.target_slide_id}.png"
    deck_bytes = (json.dumps(variant.deck.to_dict(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    deck_path.write_bytes(deck_bytes)
    target = next(slide for slide in variant.deck.slides if slide.slide_id == variant.target_slide_id)
    render_bytes = render_application_slide(target, repository_root)
    render_path.write_bytes(render_bytes)
    manifest = {
        "variant_id": variant.variant_id,
        "renderer_id": RENDERER_ID,
        "slide_deck": deck_path.name,
        "rendered_slide": render_path.name,
        "slide_deck_sha256": hashlib.sha256(deck_bytes).hexdigest(),
        "rendered_slide_sha256": hashlib.sha256(render_bytes).hexdigest(),
    }
    (variant.directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
