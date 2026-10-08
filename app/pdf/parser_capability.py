"""Port of ingestor/src/pdf/parser-capability.ts.

Parser capability matrix, reduced to what's actually wired: this ingestor
currently has three real extraction backends (native, Tesseract, Mistral
OCR -- see native.py, ocr.py, mistral_ocr.py), not a full five-parser
roster (PaddleOCR/MinerU/Docling-as-a-live-parser are still deferred). A
real benchmark stratification (script x page_type x parser) hasn't been
run for any of them yet -- the registry below is deliberately EMPTY, not
seeded with guessed numbers: "Do not infer multilingual quality from
English-heavy headline benchmarks." A ParserCapability row only gets added
here once it's measured against this project's real corpus.

Module-level mutable state, matching the TS original: fine as an
in-memory, in-process-only registry -- not persisted, not thread-safe, no
DB backing implied.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from app.pdf.types import Script

# The full roster this codebase's taxonomy covers, plus TESSERACT (which
# this codebase actually uses as the FULL_PAGE_OCR default).
Parser = Literal[
    "PDFJS_NATIVE", "TESSERACT", "MISTRAL_OCR", "PADDLEOCR", "MINERU", "DOCLING", "VLM_GENERAL"
]


class ParserCapability(TypedDict, total=False):
    parser: Parser
    script: Script
    digitalScore: float
    scannedScore: float
    tableScore: float
    numericAccuracy: float
    p95LatencyMs: float
    estimatedCostPerPage: float
    # Which real corpus measurement produced this row -- mandatory: a row
    # without a stated evaluation source is exactly the "invented number"
    # this module's docstring warns against, so there's no way to
    # construct one without saying where it came from.
    evaluatedOnCorpusVersion: str


# Empty until a real benchmark run adds measured rows. Callers should
# treat this as read-only (use list_parser_capabilities()); only
# register_parser_capability() should append to it.
_registry: list[ParserCapability] = []

ParserSelectionCriterion = Literal["digitalScore", "scannedScore", "tableScore"]


class ParserSelectionResult(TypedDict):
    parser: Parser
    score: float
    evaluatedOnCorpusVersion: str


def register_parser_capability(row: ParserCapability) -> None:
    _registry.append(row)


def list_parser_capabilities() -> list[ParserCapability]:
    return list(_registry)


# A generalized "highest-scoring known parser for a given script +
# criterion" selection (e.g. "Arabic scan -> parser with highest Arabic
# scanned-page score"). Returns None -- not a fabricated default -- when
# nothing has been measured yet for that (script, criterion) pair; callers
# decide their own fallback (today: the fixed FULL_PAGE_OCR -> Tesseract
# route in extract.py, unaffected by this module since it isn't wired into
# routing yet).
def select_parser(
    script: Script, criterion: ParserSelectionCriterion
) -> ParserSelectionResult | None:
    best: ParserSelectionResult | None = None
    for row in _registry:
        if row.get("script") != script:
            continue
        score = row.get(criterion)
        if score is None:
            continue
        if best is None or score > best["score"]:
            best = {
                "parser": row["parser"],
                "score": score,
                "evaluatedOnCorpusVersion": row["evaluatedOnCorpusVersion"],
            }
    return best
