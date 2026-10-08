"""Port of ingestor/src/pdf/router.ts.

Conservative bootstrap router: decide, per page, whether the native text
layer is usable as-is, needs column/table-aware reassembly, or is unusable
(-> OCR). Thresholds are a direct port of docs/pdf-audit/router.py, the
thresholds actually calibrated against the real Publications/legacy
corpora audited earlier -- not invented here.

This is a bootstrap router, not a calibrated one. It's conservative by
construction -- "AMBIGUOUS" pages get routed to NATIVE_LAYOUT (the safer of
the two non-OCR options) rather than silently picked as NATIVE_SIMPLE. In
practice every branch below returns confidence "OBVIOUS" -- "AMBIGUOUS" is
never actually produced.
"""

from __future__ import annotations

from app.pdf.types import PageRoutingFeatures, RoutingDecision

OCR_CHARS_PER_PAGE = 50
GARBLED_ALPHA_MAX = 0.3
GARBLED_LONG_TOKEN_MIN = 0.05
TABLE_PROXY_MIN = 0.5
MULTI_COLUMN_MIN = 1.6


def route_page(features: PageRoutingFeatures) -> RoutingDecision:
    if features["charCount"] < OCR_CHARS_PER_PAGE:
        reasons = [
            f"charCount={features['charCount']} < {OCR_CHARS_PER_PAGE} (no usable text layer)"
        ]
        return {"route": "FULL_PAGE_OCR", "confidence": "OBVIOUS", "reasons": reasons}

    if features["alphaRatio"] < GARBLED_ALPHA_MAX and features["longTokenRatio"] > GARBLED_LONG_TOKEN_MIN:
        reasons = [
            f"alphaRatio={features['alphaRatio']} low AND longTokenRatio={features['longTokenRatio']} "
            "high (lost spacing/garbled)"
        ]
        return {"route": "FULL_PAGE_OCR", "confidence": "OBVIOUS", "reasons": reasons}

    layout_flags: list[str] = []
    if features["tableProxyScore"] >= TABLE_PROXY_MIN:
        layout_flags.append(f"tableProxyScore={features['tableProxyScore']} >= {TABLE_PROXY_MIN}")
    if features["columnCountEstimate"] >= MULTI_COLUMN_MIN:
        layout_flags.append(
            f"columnCountEstimate={features['columnCountEstimate']} >= {MULTI_COLUMN_MIN}"
        )

    if layout_flags:
        return {"route": "NATIVE_LAYOUT", "confidence": "OBVIOUS", "reasons": layout_flags}

    reasons = [
        f"clean text, single column (columnCountEstimate={features['columnCountEstimate']}, "
        f"tableProxyScore={features['tableProxyScore']})"
    ]
    return {"route": "NATIVE_SIMPLE", "confidence": "OBVIOUS", "reasons": reasons}
