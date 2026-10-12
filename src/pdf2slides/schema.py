from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APPLICATION_LAYOUT_SCHEMA_VERSION = "application-layout-v1"


@dataclass(frozen=True)
class PixelCanvas:
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Canvas dimensions must be positive")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class PixelBox:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("Box geometry is invalid")

    def validate_within(self, canvas: PixelCanvas) -> None:
        if self.x + self.width > canvas.width or self.y + self.height > canvas.height:
            raise ValueError("Box exceeds its canvas")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceAsset:
    repository_path: str
    sha256: str
    intrinsic_width: int
    intrinsic_height: int
    crop: PixelBox
    fit: str = "contain"

    def __post_init__(self) -> None:
        path = Path(self.repository_path)
        if not self.repository_path or path.is_absolute() or ".." in path.parts:
            raise ValueError("Evidence path must be a contained relative path")
        if len(self.sha256) != 64:
            raise ValueError("Evidence SHA-256 is invalid")
        if self.intrinsic_width <= 0 or self.intrinsic_height <= 0:
            raise ValueError("Evidence dimensions must be positive")
        self.crop.validate_within(PixelCanvas(self.intrinsic_width, self.intrinsic_height))
        if self.fit not in {"contain", "cover", "stretch"}:
            raise ValueError("Evidence fit is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository_path": self.repository_path,
            "sha256": self.sha256,
            "intrinsic_width": self.intrinsic_width,
            "intrinsic_height": self.intrinsic_height,
            "crop": self.crop.to_dict(),
            "fit": self.fit,
        }


@dataclass(frozen=True)
class ApplicationLayout:
    canvas: PixelCanvas
    title_box: PixelBox
    body_box: PixelBox
    evidence_box: PixelBox
    evidence: EvidenceAsset
    body_behavior: str = "reflow_within_fixed_box"
    schema_version: str = APPLICATION_LAYOUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != APPLICATION_LAYOUT_SCHEMA_VERSION:
            raise ValueError("Unknown application layout schema")
        for box in (self.title_box, self.body_box, self.evidence_box):
            box.validate_within(self.canvas)
        if self.body_behavior not in {"unchanged", "reflow_within_fixed_box"}:
            raise ValueError("Body behavior is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "canvas": self.canvas.to_dict(),
            "title_box": self.title_box.to_dict(),
            "body_box": self.body_box.to_dict(),
            "evidence_box": self.evidence_box.to_dict(),
            "evidence": self.evidence.to_dict(),
            "body_behavior": self.body_behavior,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApplicationLayout":
        expected = {"schema_version", "canvas", "title_box", "body_box", "evidence_box", "evidence", "body_behavior"}
        if set(value) != expected:
            raise ValueError("Application layout fields differ")
        evidence = value["evidence"]
        return cls(
            canvas=PixelCanvas(**value["canvas"]),
            title_box=PixelBox(**value["title_box"]),
            body_box=PixelBox(**value["body_box"]),
            evidence_box=PixelBox(**value["evidence_box"]),
            evidence=EvidenceAsset(crop=PixelBox(**evidence["crop"]), **{key: item for key, item in evidence.items() if key != "crop"}),
            body_behavior=value["body_behavior"],
            schema_version=value["schema_version"],
        )


@dataclass
class Block:
    block_id: str
    page_number: int
    block_type: str
    text: str
    bbox: list[float]
    reading_order_index: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Page:
    page_number: int
    text: str
    width: float = 612.0
    height: float = 792.0
    blocks: list[Block] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "block_ids": [block.block_id for block in self.blocks],
            "text": self.text,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass
class Section:
    section_id: str
    heading: str
    page_number: int
    content_blocks: list[Block] = field(default_factory=list)
    subsections: list["Subsection"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        page_end = self.page_number
        if self.content_blocks:
            page_end = max(page_end, self.content_blocks[-1].page_number)
        if self.subsections:
            page_end = max(page_end, max(subsection.page_end for subsection in self.subsections))
        return {
            "section_id": self.section_id,
            "heading": self.heading,
            "page_number": self.page_number,
            "page_start": self.page_number,
            "page_end": page_end,
            "content_block_ids": [block.block_id for block in self.content_blocks],
            "content_blocks": [block.to_dict() for block in self.content_blocks],
            "subsections": [subsection.to_dict() for subsection in self.subsections],
        }


@dataclass
class StructuredDocument:
    source_pdf: str
    paper_id: str
    title: str
    pages: list[Page]
    blocks: list[Block]
    sections: list[Section]
    abstract: str
    assets: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_pdf": self.source_pdf,
            "paper_id": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "page_count": len(self.pages),
            "pages": [page.to_dict() for page in self.pages],
            "blocks": [block.to_dict() for block in self.blocks],
            "sections": [section.to_dict() for section in self.sections],
            "assets": self.assets,
            "metadata": {
                "ir_version": "structured-document-v0",
                **self.metadata,
            },
        }


@dataclass
class Subsection:
    subsection_id: str
    heading: str
    page_number: int
    content_blocks: list[Block] = field(default_factory=list)

    @property
    def page_end(self) -> int:
        if self.content_blocks:
            return self.content_blocks[-1].page_number
        return self.page_number

    def to_dict(self) -> dict[str, Any]:
        return {
            "subsection_id": self.subsection_id,
            "heading": self.heading,
            "page_number": self.page_number,
            "page_start": self.page_number,
            "page_end": self.page_end,
            "content_block_ids": [block.block_id for block in self.content_blocks],
            "content_blocks": [block.to_dict() for block in self.content_blocks],
        }


@dataclass
class Slide:
    slide_id: str
    title: str
    bullets: list[str]
    source_sections: list[str]
    layout_hint: str
    application_layout: ApplicationLayout | None = None

    def to_dict(self) -> dict[str, Any]:
        value = {
            "slide_id": self.slide_id,
            "title": self.title,
            "bullets": self.bullets,
            "source_sections": self.source_sections,
            "layout_hint": self.layout_hint,
        }
        if self.application_layout is not None:
            value["application_layout"] = self.application_layout.to_dict()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Slide":
        layout = value.get("application_layout")
        return cls(
            slide_id=value["slide_id"],
            title=value["title"],
            bullets=value["bullets"],
            source_sections=value["source_sections"],
            layout_hint=value["layout_hint"],
            application_layout=ApplicationLayout.from_dict(layout) if layout else None,
        )


@dataclass
class SlideDeck:
    source_pdf: str
    output_dir: str
    slides: list[Slide]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_pdf": self.source_pdf,
            "output_dir": self.output_dir,
            "slides": [slide.to_dict() for slide in self.slides],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SlideDeck":
        return cls(
            source_pdf=value["source_pdf"],
            output_dir=value["output_dir"],
            slides=[Slide.from_dict(slide) for slide in value["slides"]],
        )


@dataclass
class PipelineRun:
    input_pdf: Path
    run_dir: Path
    raw_text_path: Path
    pages_path: Path
    blocks_path: Path
    structured_doc_path: Path
    slide_deck_path: Path
    latex_path: Path
    metadata_path: Path
