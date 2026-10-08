"""Port of ingestor/src/pdf/native.ts.

**Library swap, flagged as the single biggest fidelity risk in this port**
(see the porting plan): the TS version uses pdfjs-dist (chosen there for its
Apache-2.0 license over PyMuPDF's AGPL community license). This Python port
uses **pdfplumber** (pure Python, built on pdfminer.six, Apache/MIT-
compatible) for the same licensing reason, via `page.extract_words()`.

Two real differences from the original, both believed immaterial to the
downstream algorithms but worth stating explicitly:
  1. Granularity: PDF.js reports per-content-stream-operator text "items"
     (which can span multiple words); pdfplumber's `extract_words()` gives
     per-word spans. Every downstream consumer either (a) joins spans with
     " " and re-tokenizes on whitespace (routing-features.py, numeric.py --
     unaffected by this granularity change since re-joining then
     re-splitting is invariant to it), or (b) groups spans into lines by
     y-proximity (lines.py, native.py's own assemble_linear_text -- also
     unaffected, since word-level spans on the same line still land in the
     same LINE_TOLERANCE bucket). Column/table detection (routing_features
     .py) operates on individual span x/width and is, if anything, more
     accurate at word granularity than at PDF.js's coarser run granularity.
  2. Ordering: PDF.js returns items in raw content-stream order; pdfplumber
     sorts words top-to-bottom/left-to-right by position (its default,
     `use_text_flow=False`). This is normally a strictly better
     approximation of reading order, not worse -- the one case it could
     differ is a genuinely interleaved multi-column content stream, which
     splitIntoColumns' x-gap partitioning handles independently of vertical
     order.

Coordinate system: PDF.js reports `x, y` in PDF user space (origin
bottom-left, y increases upward) -- every calibrated threshold elsewhere in
this pipeline (band fractions, gap ratios, ...) assumes that direction.
pdfplumber's words use `top`/`bottom` measured from the page's TOP (y-down).
This module converts on the way in: `y = page_height - bottom`, so the same
"near top of page -> high y" / "near bottom of page -> low y" convention
holds throughout the rest of the pipeline.
"""

from __future__ import annotations

import pdfplumber

from app.pdf.types import NativePageResult, NativeTextSpan

LINE_TOLERANCE = 3


def extract_native_pages(pdf_bytes: bytes) -> list[NativePageResult]:
    import io

    pages: list[NativePageResult] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            page_height = float(page.height)
            spans: list[NativeTextSpan] = []
            for word in page.extract_words():
                text = word.get("text")
                if not text:
                    continue
                spans.append(
                    {
                        "text": text,
                        "x": float(word["x0"]),
                        "y": page_height - float(word["bottom"]),
                        "width": float(word["x1"]) - float(word["x0"]),
                        "height": float(word["bottom"]) - float(word["top"]),
                    }
                )

            pages.append(
                {
                    "pageNumber": page_number,
                    "spans": spans,
                    "pageWidth": float(page.width),
                    "pageHeight": page_height,
                }
            )

    return pages


# Plain reading-order text for a NATIVE_SIMPLE page: spans are already
# roughly in reading order, which is reading order for genuinely
# single-column pages. Joins with newlines between spans whose y jumps
# meaningfully (new line) and a space otherwise.
def assemble_linear_text(page: NativePageResult) -> str:
    lines: list[list[str]] = []
    last_y: float | None = None

    for span in page["spans"]:
        if last_y is None or abs(span["y"] - last_y) > LINE_TOLERANCE:
            lines.append([span["text"]])
        else:
            lines[-1].append(span["text"])
        last_y = span["y"]

    return "\n".join(" ".join(line) for line in lines)


# Column split, extracted so both assemble_column_aware_text (text output)
# and segmentation.py (block-level reading order for the DoclingDocument
# mapper) use the exact same gutter decision. A deliberately simple
# two-pass split, not a general N-column solver. Returns None if no gutter
# wide enough to trust was found (caller should treat the page as
# single-column in that case).
def split_into_columns(
    page: NativePageResult,
) -> tuple[NativePageResult, NativePageResult] | None:
    if not page["spans"]:
        return None

    sorted_by_x = sorted(page["spans"], key=lambda s: s["x"])
    xs = [s["x"] for s in sorted_by_x]
    min_x = xs[0]
    max_x = xs[-1]
    x_span = max_x - min_x
    if x_span <= 0:
        return None

    # Find the widest gap between consecutive x-values as the column
    # boundary candidate.
    best_gap_start = -1.0
    best_gap_width = 0.0
    for i in range(1, len(xs)):
        prev, cur = xs[i - 1], xs[i]
        gap = cur - prev
        if gap > best_gap_width:
            best_gap_width = gap
            best_gap_start = (prev + cur) / 2

    # Require the gap to be a meaningful fraction of the page's
    # text-bearing width -- otherwise this isn't a real column boundary,
    # just normal word spacing.
    if best_gap_width < x_span * 0.08 or best_gap_start < 0:
        return None

    left_spans: list[NativeTextSpan] = []
    right_spans: list[NativeTextSpan] = []
    for s in page["spans"]:
        (left_spans if s["x"] < best_gap_start else right_spans).append(s)

    def as_page(spans: list[NativeTextSpan]) -> NativePageResult:
        return {
            "pageNumber": page["pageNumber"],
            "spans": spans,
            "pageWidth": page["pageWidth"],
            "pageHeight": page["pageHeight"],
        }

    return as_page(left_spans), as_page(right_spans)


# Column-aware reassembly for a NATIVE_LAYOUT page. Falls back to
# assemble_linear_text's line-grouping if no clear gutter is found.
def assemble_column_aware_text(page: NativePageResult) -> str:
    columns = split_into_columns(page)
    if not columns:
        return assemble_linear_text(page)

    left, right = columns
    texts = [assemble_linear_text(left), assemble_linear_text(right)]
    return "\n\n".join(t for t in texts if len(t) > 0)
