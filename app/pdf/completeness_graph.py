"""Port of ingestor/src/pdf/completeness-graph.ts.

The "completeness validator" node downstream of the canonical assembler
(see canonical_pipeline.py) -- distinct from completeness.py, which
validates the flat-text path's ledger before assembly even runs. This
validates what the DoclingDocument itself actually contains against what
the source-span ledger says should be there.
"""

from __future__ import annotations

from app.pdf.docling_types import DoclingDocument
from app.pdf.types import NativePageResult, PageGraphCompleteness, SourceSpan

# Below this ratio, a page is flagged MISSING_SPANS. Calibrated against
# real measurement (all 32 real local corpus files, native pages only):
# natural block/ledger ratio band was 0.958-1.065 (spans joined with an
# added space that wasn't literally in the source text -- growth, not
# loss). 0.85 sits comfortably below that observed floor, so it only fires
# on a real, substantial content gap, not on ordinary whitespace-
# normalization noise.
MISSING_SPANS_THRESHOLD = 0.85


def validate_canonical_completeness(
    native_pages: list[NativePageResult],
    source_spans_by_page: dict[int, list[SourceSpan]],
    doc: DoclingDocument,
) -> list[PageGraphCompleteness]:
    block_chars_by_page: dict[int, int] = {}
    for item in doc["texts"]:
        for prov in item["prov"]:
            page_no = prov["page_no"]
            block_chars_by_page[page_no] = block_chars_by_page.get(page_no, 0) + len(item["text"])

    results: list[PageGraphCompleteness] = []
    for page in native_pages:
        spans = source_spans_by_page.get(page["pageNumber"], [])
        ledger_assigned_chars = sum(len(s["text"]) for s in spans if s["status"] == "ASSIGNED")

        # A page with nothing assigned (an all-header/footer page, or a
        # genuinely blank one) has nothing to be missing -- always
        # complete, regardless of block count.
        if ledger_assigned_chars == 0:
            results.append(
                {
                    "pageNumber": page["pageNumber"],
                    "ledgerAssignedChars": 0,
                    "blockChars": 0,
                    "coverageRatio": 1,
                    "status": "COMPLETE",
                }
            )
            continue

        block_chars = block_chars_by_page.get(page["pageNumber"], 0)
        coverage_ratio = block_chars / ledger_assigned_chars
        results.append(
            {
                "pageNumber": page["pageNumber"],
                "ledgerAssignedChars": ledger_assigned_chars,
                "blockChars": block_chars,
                "coverageRatio": coverage_ratio,
                "status": "MISSING_SPANS" if coverage_ratio < MISSING_SPANS_THRESHOLD else "COMPLETE",
            }
        )
    return results
