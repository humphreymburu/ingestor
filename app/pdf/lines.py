"""Port of ingestor/src/pdf/lines.ts.

Shared line-grouping helper -- groups spans into visual lines by
y-proximity. Used by reconstruction.py (header/footer detection) and
heading.py (font-size-based heading detection), both of which need the
same line-level view of a page that native.py's assemble_linear_text
builds internally but doesn't expose.
"""

from __future__ import annotations

from typing import TypedDict

from app.pdf.types import NativeTextSpan

LINE_TOLERANCE = 3


class TextLine(TypedDict):
    text: str
    spans: list[NativeTextSpan]
    avgY: float
    # Tallest span on the line -- a cheap proxy for font size.
    maxHeight: float


def group_into_lines(spans: list[NativeTextSpan]) -> list[TextLine]:
    raw_lines: list[list[NativeTextSpan]] = []
    last_y: float | None = None

    for span in spans:
        if last_y is None or abs(span["y"] - last_y) > LINE_TOLERANCE:
            raw_lines.append([span])
        else:
            raw_lines[-1].append(span)
        last_y = span["y"]

    return [
        {
            "text": " ".join(s["text"] for s in line_spans).strip(),
            "spans": line_spans,
            "avgY": sum(s["y"] for s in line_spans) / len(line_spans),
            "maxHeight": max(s["height"] for s in line_spans),
        }
        for line_spans in raw_lines
    ]
