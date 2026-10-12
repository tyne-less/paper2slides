from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_REGION_TYPES = ("title", "author", "section", "paragraph", "table", "figure", "formula", "footnote")


@dataclass
class PageRegion:
    region_id: str
    page_number: int
    region_type: str
    bbox: list[float]
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_yolo_label_line(line: str, page_number: int, region_index: int, page_width: float, page_height: float, region_types: tuple[str, ...] = DEFAULT_REGION_TYPES) -> PageRegion:
    class_id, x_center, y_center, width, height, *confidence = line.split()
    x_center, y_center, width, height = map(float, (x_center, y_center, width, height))
    bbox = [
        round(max(0.0, (x_center - width / 2) * page_width), 6),
        round(max(0.0, (y_center - height / 2) * page_height), 6),
        round(min(page_width, (x_center + width / 2) * page_width), 6),
        round(min(page_height, (y_center + height / 2) * page_height), 6),
    ]
    return PageRegion(f"p{page_number}_r{region_index}", page_number, region_types[int(class_id)], bbox, float(confidence[0]) if confidence else None)


def parse_yolo_label_file(label_file: Path, page_number: int, page_width: float, page_height: float, region_types: tuple[str, ...] = DEFAULT_REGION_TYPES) -> list[PageRegion]:
    return [
        parse_yolo_label_line(line, page_number, index, page_width, page_height, region_types)
        for index, line in enumerate((line for line in label_file.read_text(encoding="utf-8").splitlines() if line.strip()), start=1)
    ]
