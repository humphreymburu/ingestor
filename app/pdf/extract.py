"""Port of ingestor/src/pdf/extract.ts.

Top-level orchestrator: PDF bytes -> plain fulltext string, for
knowledge-raw-v2's fulltext field. Scoped deliberately narrow -- per-page
routing decides HOW to get correct text out (linear read, column-aware
reassembly, or OCR), not how to structure it into a canonical document
graph. Chunking/embedding is a separate downstream service's job -- this
module's contract ends at "here is the document's text," plus an honest
account of exactly what got left out of it and why (for NATIVE_* pages),
plus heading markup, cross-page paragraph continuation, and numeric-token
counts.
"""

from __future__ import annotations

from typing import TypedDict

from app.pdf.completeness import build_page_completeness_report
from app.pdf.continuation import join_pages_with_continuation
from app.pdf.heading import detect_headings, apply_heading_markup
from app.pdf.mistral_ocr import ocr_page_mistral
from app.pdf.native import assemble_column_aware_text, assemble_linear_text, extract_native_pages
from app.pdf.numeric import count_numeric_tokens
from app.pdf.ocr import ocr_page
from app.pdf.reconstruction import assigned_only_page, build_source_spans
from app.pdf.routing_features import compute_page_features
from app.pdf.router import route_page
from app.pdf.types import ExtractionRoute, NumericTokenCounts, PageCompletenessReport


class PageRouteEntry(TypedDict):
    pageNumber: int
    route: ExtractionRoute
    reasons: list[str]


class FailedPageEntry(TypedDict):
    pageNumber: int
    route: ExtractionRoute
    error: str


class OcrBackendCounts(TypedDict):
    TESSERACT: int
    MISTRAL_OCR: int


class ExtractionResult(TypedDict):
    fulltext: str
    fulltextChars: int
    pageCount: int
    routeCounts: dict[str, int]
    pageRoutes: list[PageRouteEntry]
    # Pages where extraction was attempted but failed -- currently only
    # possible on the FULL_PAGE_OCR route (pdftoppm/tesseract missing or
    # erroring, AND either Mistral OCR unconfigured or itself failing).
    # NATIVE_SIMPLE/NATIVE_LAYOUT don't shell out to anything external, so
    # they don't fail this way. A non-empty list here means `fulltext` is
    # INCOMPLETE, not wrong -- every other page's text is still real and
    # included.
    failedPages: list[FailedPageEntry]
    # Which FULL_PAGE_OCR backend actually served each successfully-OCR'd
    # page. TESSERACT is the primary/self-hosted path (ocr.py);
    # MISTRAL_OCR only fires when Tesseract fails AND MISTRAL_API_KEY is
    # configured (mistral_ocr.py) -- a "managed fallback" role, not a
    # router-selected route.
    ocrBackendCounts: OcrBackendCounts
    # One entry per NATIVE_SIMPLE/NATIVE_LAYOUT page -- FULL_PAGE_OCR pages
    # have no native span ledger to account, so they're simply absent from
    # this array, not zero-filled.
    completeness: list[PageCompletenessReport]
    # Document-level count only. No second-reader verification exists;
    # this is measurement, not protection.
    numericTokens: NumericTokenCounts


async def extract_fulltext(pdf_bytes: bytes) -> ExtractionResult:
    native_pages = extract_native_pages(pdf_bytes)

    # Whole-document view needed up front -- header/footer/page-number
    # detection and heading-size baselining both compare a page against
    # the rest of the document, not in isolation.
    source_spans_by_page = build_source_spans(native_pages)
    headings = detect_headings(native_pages)

    route_counts = {"NATIVE_SIMPLE": 0, "NATIVE_LAYOUT": 0, "FULL_PAGE_OCR": 0}
    page_routes: list[PageRouteEntry] = []
    failed_pages: list[FailedPageEntry] = []
    ocr_backend_counts: OcrBackendCounts = {"TESSERACT": 0, "MISTRAL_OCR": 0}
    completeness: list[PageCompletenessReport] = []
    page_texts: list[str] = []

    for page in native_pages:
        # Routing runs against the ORIGINAL unfiltered page, deliberately
        # -- the router's thresholds were calibrated against the audit's
        # unfiltered measurements; filtering only applies at text
        # assembly, after the routing decision is made.
        features = compute_page_features(page)
        decision = route_page(features)
        route_counts[decision["route"]] += 1
        page_routes.append(
            {"pageNumber": page["pageNumber"], "route": decision["route"], "reasons": decision["reasons"]}
        )

        if decision["route"] in ("NATIVE_SIMPLE", "NATIVE_LAYOUT"):
            spans = source_spans_by_page.get(page["pageNumber"], [])
            completeness.append(build_page_completeness_report(page["pageNumber"], spans))
            cleaned_page = assigned_only_page(page, spans)
            assembled = (
                assemble_linear_text(cleaned_page)
                if decision["route"] == "NATIVE_SIMPLE"
                else assemble_column_aware_text(cleaned_page)
            )
            page_texts.append(apply_heading_markup(page["pageNumber"], assembled, headings))

        elif decision["route"] == "FULL_PAGE_OCR":
            # A single page's OCR failing (missing/broken pdftoppm/
            # tesseract, a corrupt page image, etc.) must not discard
            # every other page's real, already-extracted native text --
            # that's a much worse outcome than one page silently missing
            # from the fulltext.
            try:
                page_texts.append(await ocr_page(pdf_bytes, page["pageNumber"]))
                ocr_backend_counts["TESSERACT"] += 1
            except Exception as primary_error:  # noqa: BLE001 -- mirrors TS's catch-and-fallback
                # Managed fallback -- only actually attempted when
                # MISTRAL_API_KEY is configured; otherwise
                # ocr_page_mistral raises immediately (no network call)
                # and the original Tesseract error is what gets recorded,
                # same as before this fallback existed.
                try:
                    page_texts.append(await ocr_page_mistral(pdf_bytes, page["pageNumber"]))
                    ocr_backend_counts["MISTRAL_OCR"] += 1
                except Exception:  # noqa: BLE001
                    failed_pages.append(
                        {
                            "pageNumber": page["pageNumber"],
                            "route": decision["route"],
                            "error": str(primary_error),
                        }
                    )

    # Joins with paragraph-continuation detection rather than a uniform
    # "\n\n" between every page -- see continuation.py.
    fulltext = join_pages_with_continuation(page_texts).strip()

    return {
        "fulltext": fulltext,
        "fulltextChars": len(fulltext),
        "pageCount": len(native_pages),
        "routeCounts": route_counts,
        "pageRoutes": page_routes,
        "failedPages": failed_pages,
        "ocrBackendCounts": ocr_backend_counts,
        "completeness": completeness,
        "numericTokens": count_numeric_tokens(fulltext),
    }
