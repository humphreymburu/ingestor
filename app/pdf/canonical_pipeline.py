"""Port of ingestor/src/pdf/canonical-pipeline.ts.

The full flow: source-span ledger -> canonical assembler -> completeness
validator -> (complete: done) | (missing spans: alternate parser -> still
missing: unresolved -> human review).

Two retry tiers for "alternate parser," matched to what this project
actually has available (no PaddleOCR/MinerU/second vendor):
  1. Alternate segmentation strategy (forced linear vs. forced
     column-split) -- a genuinely different reading-order algorithm over
     the same native spans, free, no new dependency.
  2. OCR fallback -- render + recognize the still-incomplete page
     directly, bypassing native assembly for it entirely. Not cross-
     validated against the native ledger it replaces -- accepted as
     resolving if it produces real text, the same trust level
     FULL_PAGE_OCR pages get elsewhere in this pipeline.
Still missing after both -> unresolved -> a real (if UI-less) review
queue entry: "reviewability is part of the data model."
"""

from __future__ import annotations

import dataclasses
from typing import TypedDict

from app.pdf.completeness_graph import validate_canonical_completeness
from app.pdf.docling_mapper import BuildDoclingDocumentParams, build_docling_document
from app.pdf.docling_types import DoclingDocument
from app.pdf.ocr import ocr_page
from app.pdf.reconstruction import assigned_only_page
from app.pdf.segmentation import segment_native_page
from app.pdf.types import ExtractionRoute, PageGraphCompleteness, ReviewState


class CanonicalPipelineResult(TypedDict):
    document: DoclingDocument
    completeness: list[PageGraphCompleteness]
    # Only pages that actually need a human look -- not a per-page audit
    # trail of every page that was fine.
    reviewQueue: list[ReviewState]


async def assemble_canonical_document_with_recovery(
    pdf_bytes: bytes, params: BuildDoclingDocumentParams
) -> CanonicalPipelineResult:
    # "processed page count = source page count" invariant, applied to
    # this pipeline's actual data shapes: validate_canonical_completeness
    # only evaluates pages present in native_pages, so a page with real
    # assigned content in the ledger that never made it into native_pages
    # at all (not under-represented -- absent from processing entirely)
    # would otherwise be invisible to every check below, not even
    # entering the retry cascade.
    #
    # Not reachable through any real call site today -- source_spans_by
    # _page is always built via build_source_spans(native_pages), the
    # same list, in every actual caller -- but a real defensive check
    # nonetheless, and it can never structurally be *retried*: there's no
    # NativePageResult for it, so there's no geometry to re-segment and
    # build_docling_document's loop (which iterates native_pages) could
    # never splice an OCR result back in for it even if OCR succeeded.
    # Goes straight to the review queue, not through the two retry tiers
    # below.
    processed_page_numbers = {p["pageNumber"] for p in params.native_pages}
    orphaned_ledger_pages = [
        page_number
        for page_number, spans in params.source_spans_by_page.items()
        if page_number not in processed_page_numbers and any(s["status"] == "ASSIGNED" for s in spans)
    ]

    review_queue: list[ReviewState] = [
        {"pageNumber": page_number, "required": True, "reasons": ["STRUCTURE_AMBIGUITY"], "status": "PENDING"}
        for page_number in orphaned_ledger_pages
    ]
    orphaned_completeness: list[PageGraphCompleteness] = [
        {
            "pageNumber": page_number,
            "ledgerAssignedChars": sum(
                len(s["text"]) for s in params.source_spans_by_page.get(page_number, []) if s["status"] == "ASSIGNED"
            ),
            "blockChars": 0,
            "coverageRatio": 0,
            "status": "MISSING_SPANS",
        }
        for page_number in orphaned_ledger_pages
    ]

    # Pass 1: normal assembly.
    doc = build_docling_document(params)
    completeness = validate_canonical_completeness(params.native_pages, params.source_spans_by_page, doc)
    missing = [c["pageNumber"] for c in completeness if c["status"] == "MISSING_SPANS"]

    if missing:
        # Retry 1: try both forced segmentation strategies per missing
        # page, keep whichever produces more text -- a real, measured
        # choice, not an assumption about which one was "wrong."
        strategy_override_by_page: dict[int, str] = {}
        for page_number in missing:
            page = next((p for p in params.native_pages if p["pageNumber"] == page_number), None)
            if page is None:
                continue
            spans = params.source_spans_by_page.get(page_number, [])
            cleaned = assigned_only_page(page, spans)
            linear_blocks = segment_native_page(cleaned, params.headings, "FORCE_LINEAR")
            column_blocks = segment_native_page(cleaned, params.headings, "FORCE_COLUMN_AWARE")
            linear_chars = sum(len(b["text"]) for b in linear_blocks)
            column_chars = sum(len(b["text"]) for b in column_blocks)
            strategy_override_by_page[page_number] = (
                "FORCE_COLUMN_AWARE" if column_chars > linear_chars else "FORCE_LINEAR"
            )

        doc = build_docling_document(
            dataclasses.replace(params, strategy_override_by_page=strategy_override_by_page)
        )
        completeness = validate_canonical_completeness(params.native_pages, params.source_spans_by_page, doc)
        missing = [c["pageNumber"] for c in completeness if c["status"] == "MISSING_SPANS"]

    if missing:
        # Retry 2: OCR fallback for pages still incomplete after the
        # alternate-strategy retry.
        ocr_text_by_page = dict(params.ocr_text_by_page)
        page_routes: dict[int, ExtractionRoute] = dict(params.page_routes)
        recovered: set[int] = set()
        still_unresolved: list[int] = []

        for page_number in missing:
            try:
                text = await ocr_page(pdf_bytes, page_number)
                if text.strip():
                    ocr_text_by_page[page_number] = text
                    page_routes[page_number] = "FULL_PAGE_OCR"
                    recovered.add(page_number)
                else:
                    still_unresolved.append(page_number)
            except Exception:  # noqa: BLE001 -- mirrors TS's catch-and-record
                still_unresolved.append(page_number)

        doc = build_docling_document(
            dataclasses.replace(params, ocr_text_by_page=ocr_text_by_page, page_routes=page_routes)
        )
        # OCR-recovered pages are no longer comparable against the native
        # ledger they replaced -- marked COMPLETE on the strength of
        # producing real OCR text, not re-checked against native spans.
        completeness = [
            {**c, "status": "COMPLETE"} if c["pageNumber"] in recovered else c
            for c in validate_canonical_completeness(params.native_pages, params.source_spans_by_page, doc)
        ]

        for page_number in still_unresolved:
            review_queue.append(
                {"pageNumber": page_number, "required": True, "reasons": ["STRUCTURE_AMBIGUITY"], "status": "PENDING"}
            )

    return {
        "document": doc,
        "completeness": completeness + orphaned_completeness,
        "reviewQueue": review_queue,
    }
