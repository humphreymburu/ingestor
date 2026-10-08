"""Port of ingestor/src/pdf/mistral-ocr.ts.

Managed-fallback OCR (Mistral OCR), alongside the existing self-hosted
path (ocr.py: pdftoppm + Tesseract). This is the adapter only: not yet
benchmarked against the real corpus, so no ParserCapability row exists for
it yet (parser_capability.py).

API contract (docs.mistral.ai/capabilities/OCR, checked 2026-09-02): POST
{api_base}/v1/ocr, Authorization: Bearer <key>, JSON body { model,
document: { type: "document_url", document_url }, pages: [0-indexed page
numbers] }. Base64 PDFs are documented as supported but the docs don't
show the literal string format for that tab -- this follows Mistral's one
documented base64 convention elsewhere (chat vision's image_url:
"data:<mime>;base64,..."). **Not live-verified against a real API key** --
confirm this data: URI framing against a real call before depending on it
in production.
"""

from __future__ import annotations

import base64
from typing import Any, TypedDict

import httpx

from app.config import config

MISTRAL_OCR_MODEL = "mistral-ocr-latest"


class MistralOcrNotConfiguredError(Exception):
    def __init__(self) -> None:
        super().__init__("MISTRAL_API_KEY is not set -- Mistral OCR route is unavailable")


class MistralOcrRequest(TypedDict):
    apiPageIndex: int
    body: dict[str, Any]


# Pure request-building, split out from ocr_page_mistral() so the base64/
# page-index framing is unit-testable without mocking config or an HTTP
# client.
def build_mistral_ocr_request(pdf_bytes: bytes, page_number: int) -> MistralOcrRequest:
    encoded = base64.b64encode(pdf_bytes).decode("ascii")
    document_url = f"data:application/pdf;base64,{encoded}"
    # The API is 0-indexed; every other page number in this package is
    # 1-indexed (matches pdfplumber/pdftoppm convention -- see native.py,
    # ocr.py).
    api_page_index = page_number - 1
    return {
        "apiPageIndex": api_page_index,
        "body": {
            "model": MISTRAL_OCR_MODEL,
            "document": {"type": "document_url", "document_url": document_url},
            "pages": [api_page_index],
        },
    }


# Pure response-parsing, split out for the same reason: exercise "page
# found by requested index," "fall back to the only page returned," and
# "no pages at all" without a real HTTP round trip.
def pick_mistral_ocr_page(result: dict[str, Any], api_page_index: int, page_number: int) -> str:
    pages = result.get("pages") or []
    page = next((p for p in pages if p.get("index") == api_page_index), None)
    if page is None and pages:
        page = pages[0]
    if page is None:
        raise RuntimeError(f"Mistral OCR returned no pages for requested page {page_number}")
    return page["markdown"].strip()


# Mirrors ocr_page()'s signature (ocr.py) so callers (extract.py) can try
# one backend then fall back to the other without a shape mismatch.
async def ocr_page_mistral(pdf_bytes: bytes, page_number: int) -> str:
    if not config.mistral_ocr.api_key:
        raise MistralOcrNotConfiguredError()

    request = build_mistral_ocr_request(pdf_bytes, page_number)

    async with httpx.AsyncClient(timeout=config.mistral_ocr.timeout_ms / 1000) as client:
        response = await client.post(
            f"{config.mistral_ocr.api_base}/v1/ocr",
            headers={
                "Authorization": f"Bearer {config.mistral_ocr.api_key}",
                "Content-Type": "application/json",
            },
            json=request["body"],
        )

    if response.is_error:
        body_text = response.text
        raise RuntimeError(
            f"Mistral OCR {response.status_code} {response.reason_phrase} for page {page_number}: "
            f"{body_text[:500]}"
        )

    result = response.json()
    return pick_mistral_ocr_page(result, request["apiPageIndex"], page_number)
