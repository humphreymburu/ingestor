"""Port of ingestor/src/pdf/heading.ts.

Heading-line detection: font-size comparison against the document's own
body-text size, no layout model -- char-weighted modal size as the
body-text baseline.

Deliberately flat, not a block tree: there's no canonical document graph in
the production (flat-fulltext) path, so a detected heading is rendered as a
markdown-style prefix ("# "/"## "/"### ") on its line in the plain fulltext
string -- enough for a downstream reader or future chunker to recognize
structure without building a real CanonicalBlock hierarchy.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

from app.pdf.lines import TextLine, group_into_lines
from app.pdf.types import NativePageResult

# A heading candidate must be reasonably short -- a large pull-quote
# paragraph set in a bigger font is not a heading. Real headings are short
# by nature; this is cheap insurance against misclassifying one.
MAX_HEADING_LENGTH = 120

# Must contain at least one real word (3+ consecutive letters) -- guards
# against a stylized spread-letter infographic (each letter on its own
# line, at a large font size) getting flagged as a heading per letter.
REAL_WORD_PATTERN = re.compile(r"[a-zA-Z]{3,}")

# Size-ratio tiers relative to the document's modal (body) height. Anything
# within BODY_TOLERANCE of modal, or smaller, is never a heading --
# footnotes/captions are typically smaller than body text, not larger, so
# there's no ambiguity to resolve there.
BODY_TOLERANCE = 1.05
H3_MIN_RATIO = 1.08
H2_MIN_RATIO = 1.25
H1_MIN_RATIO = 1.5

HeadingLevel = Literal[1, 2, 3]


class HeadingCandidate(TypedDict):
    pageNumber: int
    text: str
    level: HeadingLevel


# Char-weighted modal line height across the whole document -- "which size
# has the most actual body characters, not just the most distinct lines",
# so a page with one huge title line doesn't skew the baseline.
def _document_modal_height(all_lines: list[TextLine]) -> float | None:
    weight_by_size: dict[float, int] = {}
    for line in all_lines:
        size = round(line["maxHeight"] * 10) / 10
        weight = len(line["text"])
        weight_by_size[size] = weight_by_size.get(size, 0) + weight
    if not weight_by_size:
        return None

    modal_size = 0.0
    modal_weight = -1
    for size, weight in weight_by_size.items():
        if weight > modal_weight:
            modal_weight = weight
            modal_size = size
    return modal_size


def _level_for_ratio(ratio: float) -> HeadingLevel | None:
    if ratio >= H1_MIN_RATIO:
        return 1
    if ratio >= H2_MIN_RATIO:
        return 2
    if ratio >= H3_MIN_RATIO:
        return 3
    return None


# Only NATIVE_SIMPLE/NATIVE_LAYOUT pages have a native span layer to
# measure font size from -- OCR pages produce flat text with no geometry,
# so callers should only pass native pages here.
def detect_headings(pages: list[NativePageResult]) -> list[HeadingCandidate]:
    lines_by_page = [(page, group_into_lines(page["spans"])) for page in pages]
    all_lines = [line for _page, lines in lines_by_page for line in lines]

    modal_height = _document_modal_height(all_lines)
    if modal_height is None or modal_height <= 0:
        return []

    candidates: list[HeadingCandidate] = []
    for page, lines in lines_by_page:
        for line in lines:
            text = line["text"]
            if not text or len(text) > MAX_HEADING_LENGTH:
                continue
            if not REAL_WORD_PATTERN.search(text):
                continue
            ratio = line["maxHeight"] / modal_height
            if ratio < BODY_TOLERANCE:
                continue  # body-sized or smaller -- never a heading
            level = _level_for_ratio(ratio)
            if level is None:
                continue
            candidates.append({"pageNumber": page["pageNumber"], "text": text, "level": level})
    return candidates


# Post-processes an already-assembled page text string, prefixing any line
# that exactly matches a detected heading for that page. Operates on the
# final joined string rather than threading a classifier through native
# .py's assembly functions.
def apply_heading_markup(page_number: int, text: str, headings: list[HeadingCandidate]) -> str:
    level_by_text: dict[str, HeadingLevel] = {
        h["text"]: h["level"] for h in headings if h["pageNumber"] == page_number
    }
    if not level_by_text:
        return text

    def markup_line(line: str) -> str:
        level = level_by_text.get(line.strip())
        if not level:
            return line
        return f"{'#' * level} {line.strip()}"

    return "\n".join(markup_line(line) for line in text.split("\n"))
