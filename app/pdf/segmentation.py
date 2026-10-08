"""Port of ingestor/src/pdf/segmentation.ts.

Full paragraph/heading segmentation -- the intermediate representation
docling_mapper.py builds a real DoclingDocument from. Column-aware (reuses
native.py's split_into_columns so segmentation follows the same
left-column-then-right-column reading order assemble_column_aware_text
already produces for the flat fulltext), and produces genuine paragraph
grouping via inter-line gap detection -- not one block per line.

**Not part of the production flat-fulltext path** (extract.py) -- this
feeds the canonical DoclingDocument pipeline (docling_mapper.py,
canonical_pipeline.py), which has no production caller today either; see
the porting plan for why it's still ported at full fidelity, just
sequenced last.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from app.pdf.heading import HeadingCandidate, HeadingLevel
from app.pdf.lines import TextLine, group_into_lines
from app.pdf.native import split_into_columns
from app.pdf.types import NativePageResult, NativeTextSpan


class BoundingBoxLike(TypedDict):
    x: float
    y: float
    width: float
    height: float


class SegmentedBlock(TypedDict, total=False):
    kind: Literal["heading", "paragraph"]
    level: HeadingLevel | None  # only present when kind == "heading"
    pageNumber: int
    orig: str  # lines joined with a single space, as extracted -- no whitespace normalization
    text: str  # orig with runs of whitespace collapsed to one space
    bbox: BoundingBoxLike


# Paragraph breaks are detected by inter-line gap, not by any structural
# marker (PDFs don't carry one) -- a gap meaningfully larger than the
# page's own typical single-line spacing is the signal. 1.6x is the same
# spirit of threshold as router.py's other ratio-based decisions:
# conservative enough not to fire on normal line-height variation within a
# paragraph, low enough to catch a real paragraph gap.
PARAGRAPH_GAP_RATIO = 1.6

SegmentationStrategy = Literal["AUTO", "FORCE_LINEAR", "FORCE_COLUMN_AWARE"]


def _median_gap(lines: list[TextLine]) -> float:
    gaps = [abs(lines[i - 1]["avgY"] - lines[i]["avgY"]) for i in range(1, len(lines))]
    if not gaps:
        return 0
    sorted_gaps = sorted(gaps)
    return sorted_gaps[len(sorted_gaps) // 2]


def _union_bbox(spans: list[NativeTextSpan]) -> BoundingBoxLike:
    min_x = min(s["x"] for s in spans)
    min_y = min(s["y"] for s in spans)
    max_x = max(s["x"] + s["width"] for s in spans)
    max_y = max(s["y"] + s["height"] for s in spans)
    return {"x": min_x, "y": min_y, "width": max_x - min_x, "height": max_y - min_y}


def _build_block(
    page_number: int, lines: list[TextLine], kind: Literal["heading", "paragraph"], level: HeadingLevel | None = None
) -> SegmentedBlock:
    orig = " ".join(line["text"] for line in lines).strip()
    text = re.sub(r"\s+", " ", orig)
    bbox = _union_bbox([span for line in lines for span in line["spans"]])
    return {"kind": kind, "level": level, "pageNumber": page_number, "orig": orig, "text": text, "bbox": bbox}


def _segment_single_column(
    page: NativePageResult, heading_level_by_text: dict[str, HeadingLevel]
) -> list[SegmentedBlock]:
    lines = [line for line in group_into_lines(page["spans"]) if len(line["text"]) > 0]
    if not lines:
        return []

    gap_threshold = _median_gap(lines) * PARAGRAPH_GAP_RATIO
    blocks: list[SegmentedBlock] = []
    current: list[TextLine] = []
    prev_line: TextLine | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        blocks.append(_build_block(page["pageNumber"], current, "paragraph"))
        current = []

    for line in lines:
        level = heading_level_by_text.get(line["text"])
        if level:
            flush()
            blocks.append(_build_block(page["pageNumber"], [line], "heading", level))
            prev_line = None
            continue

        gap = abs(prev_line["avgY"] - line["avgY"]) if prev_line else 0
        starts_new_paragraph = len(current) == 0 or (gap_threshold > 0 and gap > gap_threshold)
        if starts_new_paragraph:
            flush()
        current.append(line)
        prev_line = line
    flush()

    return blocks


# Used only by FORCE_COLUMN_AWARE when the normal gutter-width threshold
# (native.py's split_into_columns, 8% of text-bearing width) didn't find a
# gutter -- splits at the raw midpoint of the x-range instead. A last-
# resort alternate attempt, not the default: expected to be noisier than
# the real gutter detection, which is exactly why it's only tried when
# AUTO already failed to produce a complete page.
def _force_split(page: NativePageResult) -> tuple[NativePageResult, NativePageResult] | None:
    if not page["spans"]:
        return None
    xs = [s["x"] for s in page["spans"]]
    mid = (min(xs) + max(xs)) / 2
    left = [s for s in page["spans"] if s["x"] < mid]
    right = [s for s in page["spans"] if s["x"] >= mid]
    if not left or not right:
        return None

    def as_page(spans: list[NativeTextSpan]) -> NativePageResult:
        return {**page, "spans": spans}

    return as_page(left), as_page(right)


# "alternate parser" step 1 in the completeness-recovery flow (see
# canonical_pipeline.py): if the gutter-based column split mis-detected a
# real 2-column page as single-column (or vice versa) and dropped content
# as a result, retrying with the OPPOSITE strategy is a real, free (no new
# dependency) alternate assembly to try before escalating to OCR.

# Public entry point -- one native page in, an ordered list of
# heading/paragraph blocks out, in real reading order (column-aware).
def segment_native_page(
    page: NativePageResult, headings: list[HeadingCandidate], strategy: SegmentationStrategy = "AUTO"
) -> list[SegmentedBlock]:
    heading_level_by_text: dict[str, HeadingLevel] = {
        h["text"]: h["level"] for h in headings if h["pageNumber"] == page["pageNumber"]
    }

    if strategy == "FORCE_LINEAR":
        return _segment_single_column(page, heading_level_by_text)

    if strategy == "FORCE_COLUMN_AWARE":
        columns = split_into_columns(page) or _force_split(page)
    else:
        columns = split_into_columns(page)

    if not columns:
        return _segment_single_column(page, heading_level_by_text)

    left, right = columns
    return _segment_single_column(left, heading_level_by_text) + _segment_single_column(
        right, heading_level_by_text
    )
