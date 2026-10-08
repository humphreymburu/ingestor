"""Port of ingestor/src/pdf/reconstruction.ts.

Header/footer/page-number detection across a whole document's native
pages, generalizing the audit's repeat-line heuristic into a real pipeline
stage that runs on every page of the real document and actually excludes
matches from the assembled fulltext, feeding the completeness ledger
(completeness.py) so exclusion is recorded, not silent.

Explicitly NOT a fixed "appears on >60% of pages" rule alone: position
(top/bottom band) + text-similarity clustering, and a conservative no-op
below a minimum page count.
"""

from __future__ import annotations

import math
import re
from typing import Literal, TypedDict

from app.pdf.lines import group_into_lines as group_spans_into_lines
from app.pdf.types import ExclusionReason, NativePageResult, NativeTextSpan, SourceSpan

# Below this, "repeated across pages" is too weak a signal on so few
# samples to trust -- use conservative (i.e. no) removal.
MIN_PAGES_FOR_DETECTION = 6

# PDF user space: y increases upward from the page bottom. A candidate in
# the top 15% of page height is a header candidate; bottom 15% is a footer
# candidate. Body text essentially never lives this close to an edge on a
# real typeset page, so restricting candidacy by position is what keeps
# this from ever flagging a genuinely repeated body sentence.
BAND_FRACTION = 0.15

# Excludes accidentally matching a long repeated body paragraph, keeps
# this scoped to genuinely running-head/foot-scale text.
MAX_CANDIDATE_LENGTH = 100

# Purely numeric/punctuation after digit-normalization ("3", "page #",
# "# | Global Peatlands" -> "#", "page #", "# | global peatlands" -- the
# last one isn't purely #/punctuation so it would NOT match this and falls
# through to RUNNING_HEADER/FOOTER classification instead, which is the
# right call: a running head that happens to include a page number is
# still a running head, not "just a page number."
PAGE_NUMBER_PATTERN = re.compile(r"^[#\s|.\-–—]+$")

Band = Literal["TOP", "BOTTOM", None]


class _Line(TypedDict):
    text: str
    normalized: str
    spans: list[NativeTextSpan]
    band: Band


def _group_into_lines(page: NativePageResult) -> list[_Line]:
    top_band_min_y = page["pageHeight"] * (1 - BAND_FRACTION)
    bottom_band_max_y = page["pageHeight"] * BAND_FRACTION

    lines: list[_Line] = []
    for line in group_spans_into_lines(page["spans"]):
        normalized = re.sub(r"\d+", "#", line["text"].lower())
        if line["avgY"] >= top_band_min_y:
            band: Band = "TOP"
        elif line["avgY"] <= bottom_band_max_y:
            band = "BOTTOM"
        else:
            band = None
        lines.append(
            {"text": line["text"], "normalized": normalized, "spans": line["spans"], "band": band}
        )
    return lines


def _classify(normalized: str, band: Literal["TOP", "BOTTOM"]) -> ExclusionReason:
    if PAGE_NUMBER_PATTERN.match(normalized):
        return "PAGE_NUMBER"
    return "RUNNING_HEADER" if band == "TOP" else "RUNNING_FOOTER"


# Returns, per page, every span tagged ASSIGNED or INTENTIONALLY_EXCLUDED
# -- the full source-span ledger this document's completeness report is
# built from.
def build_source_spans(pages: list[NativePageResult]) -> dict[int, list[SourceSpan]]:
    result: dict[int, list[SourceSpan]] = {}

    if len(pages) < MIN_PAGES_FOR_DETECTION:
        for page in pages:
            result[page["pageNumber"]] = [
                {**s, "pageNumber": page["pageNumber"], "status": "ASSIGNED", "exclusionReason": None}
                for s in page["spans"]
            ]
        return result

    lines_by_page = [(page, _group_into_lines(page)) for page in pages]

    # Count DISTINCT pages each normalized top/bottom-band line appears on
    # (not raw occurrence count -- a line repeated twice on one page via a
    # rendering quirk shouldn't count double).
    page_count_by_key: dict[str, set[int]] = {}
    for page, lines in lines_by_page:
        for line in lines:
            if not line["band"] or len(line["text"]) == 0 or len(line["normalized"]) >= MAX_CANDIDATE_LENGTH:
                continue
            key = f"{line['band']}:{line['normalized']}"
            page_count_by_key.setdefault(key, set()).add(page["pageNumber"])

    threshold = max(3, math.ceil(len(pages) * 0.4))
    running_keys = {key for key, page_set in page_count_by_key.items() if len(page_set) >= threshold}

    for page, lines in lines_by_page:
        spans: list[SourceSpan] = []
        for line in lines:
            key = f"{line['band']}:{line['normalized']}" if line["band"] else None
            is_running = key is not None and key in running_keys
            exclusion_reason = _classify(line["normalized"], line["band"]) if is_running and line["band"] else None

            for span in line["spans"]:
                if is_running:
                    spans.append(
                        {
                            **span,
                            "pageNumber": page["pageNumber"],
                            "status": "INTENTIONALLY_EXCLUDED",
                            "exclusionReason": exclusion_reason,
                        }
                    )
                else:
                    spans.append(
                        {**span, "pageNumber": page["pageNumber"], "status": "ASSIGNED", "exclusionReason": None}
                    )
        result[page["pageNumber"]] = spans

    return result


# Rebuilds a NativePageResult containing only ASSIGNED spans, for feeding
# into native.py's assemble_linear_text/assemble_column_aware_text.
# Routing (routing_features.py/router.py) deliberately still runs against
# the ORIGINAL unfiltered page, not this -- the router's thresholds were
# calibrated against the audit's unfiltered measurements, and changing
# that input would invalidate the calibration without new measurement.
# This filtering applies only at the text-assembly step, after routing.
def assigned_only_page(page: NativePageResult, spans: list[SourceSpan]) -> NativePageResult:
    return {
        "pageNumber": page["pageNumber"],
        "pageWidth": page["pageWidth"],
        "pageHeight": page["pageHeight"],
        "spans": [s for s in spans if s["status"] == "ASSIGNED"],
    }
