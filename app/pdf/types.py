"""Port of ingestor/src/pdf/types.ts.

Types mirror typescript_pdf_extraction_rag_architecture_v2.2.md sections 4.1
(route taxonomy), 8 (cheap routing features), 9.1/10 (script), scoped down
to what this ingestor actually needs: PDF -> plain fulltext string ->
knowledge-raw-v2.fulltext. No canonical document graph, no chunking, no
embedding -- those are a downstream service's job.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# Same three real routes the architecture doc defines, minus
# VLM_FALLBACK/SHADOW_COMPARE (both are calibration/complex-parser concerns
# for a canonical-graph pipeline we're not building).
ExtractionRoute = Literal["NATIVE_SIMPLE", "NATIVE_LAYOUT", "FULL_PAGE_OCR"]

Script = Literal[
    "LATIN",
    "ARABIC",
    "CYRILLIC",
    "HAN",
    "HANGUL",
    "DEVANAGARI",
    "THAI",
    "GREEK",
    "HEBREW",
    "OTHER",
    "MIXED",
    "UNKNOWN",
]


class ScriptDistribution(TypedDict):
    LATIN: int
    ARABIC: int
    CYRILLIC: int
    HAN: int
    DEVANAGARI: int
    OTHER: int


# One text span as PDF.js reports it (subset of TextItem actually used) --
# ported here as whatever native.py's chosen library (pdfplumber) reports,
# normalized to this same shape.
class NativeTextSpan(TypedDict):
    text: str
    x: float
    y: float
    width: float
    height: float


class NativePageResult(TypedDict):
    pageNumber: int  # 1-indexed
    spans: list[NativeTextSpan]
    pageWidth: float
    pageHeight: float


# Cheap per-page routing features -- subset of the architecture doc's
# PageRoutingFeatures (section 8) that's actually derivable from text-layer
# geometry without a layout model.
class PageRoutingFeatures(TypedDict):
    charCount: int
    alphaRatio: float
    functionWordRatio: float
    replacementCharCount: int
    longTokenRatio: float
    columnCountEstimate: int
    tableProxyScore: float  # 0-1, cheap numeric/ruling-line-style proxy
    dominantScript: Script
    scriptDistribution: ScriptDistribution


class RoutingDecision(TypedDict):
    route: ExtractionRoute
    confidence: Literal["OBVIOUS", "AMBIGUOUS"]
    reasons: list[str]


ExclusionReason = Literal["RUNNING_HEADER", "RUNNING_FOOTER", "PAGE_NUMBER"]


# Completeness ledger -- every native span on a page ends up either in the
# assembled text (ASSIGNED) or deliberately left out of it
# (INTENTIONALLY_EXCLUDED, with a reason) -- never silently dropped. Only
# applies to NATIVE_SIMPLE/NATIVE_LAYOUT pages; OCR pages have no native
# span layer to account for.
class SourceSpan(NativeTextSpan):
    pageNumber: int
    status: Literal["ASSIGNED", "INTENTIONALLY_EXCLUDED"]
    exclusionReason: ExclusionReason | None


class PageCompletenessReport(TypedDict):
    pageNumber: int
    sourceSpanCount: int
    sourceCharacterCount: int
    assignedSpanCount: int
    assignedCharacterCount: int
    excludedSpanCount: int
    excludedCharacterCount: int
    excludedReasons: dict[str, int]
    # PASS: normal. WARN: an unusually large share of the page was excluded
    # -- worth a human glance. Never FAIL here -- by construction every
    # span is either ASSIGNED or INTENTIONALLY_EXCLUDED with a reason, so
    # there's no "unaccounted" state possible in this reduced model.
    status: Literal["PASS", "WARN"]


class NumericTokenCounts(TypedDict):
    percentages: int
    currencies: int
    dates: int
    decimals: int
    integers: int
    total: int


# Graph-level completeness -- the "completeness validator" downstream of
# the canonical assembler, not the flat-path ledger check completeness.py
# already does upstream of assembly. Threshold calibrated against real
# measurement: 32 real local documents showed a natural block/ledger
# character-count ratio of 0.958-1.065 (join-whitespace noise, not loss) --
# MISSING_SPANS uses 0.85, comfortably below that observed floor.
class PageGraphCompleteness(TypedDict):
    pageNumber: int
    ledgerAssignedChars: int
    blockChars: int
    coverageRatio: float
    status: Literal["COMPLETE", "MISSING_SPANS"]


# Reduced from the architecture doc's full ReviewState -- same shape,
# subset of reasons this pipeline can actually produce (only
# STRUCTURE_AMBIGUITY, from exhausted completeness-recovery retries).
class ReviewState(TypedDict):
    pageNumber: int
    required: bool
    reasons: list[Literal["STRUCTURE_AMBIGUITY"]]
    status: Literal["NOT_REQUIRED", "PENDING"]
