"""Port of ingestor/src/shared/text.ts.

Text helpers used by more than one source's mapper (Drupal, DSpace).
Source-specific parsing (Drupal's field selection, DSpace's metadata value
extraction) stays in each source's own module.

These are deliberately bit-for-bit ports: `content_hash`/`byte_hash`/
`canonicalize` values are persisted in OpenSearch today, and any drift here
changes existing documents' values on cutover.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata


def content_hash(content: str | None) -> str | None:
    if not content:
        return None
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# sha256 of the raw source bytes -- distinct from content_hash: content_hash
# is over *extracted text* (changes if extraction logic changes even on an
# unchanged PDF); byte_hash is over the *source PDF bytes themselves*
# (stable across re-extraction, catches exact-duplicate bitstreams -- the
# same file uploaded twice, or re-ingested unchanged).
def byte_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Normalized slug form of a taxonomy-term name / display string for
# consistent filtering/faceting (e.g. "Report" -> "report") -- neither
# source has a fixed enum to canonicalize against, so this is a generic
# accent-stripped, lowercased, dash-joined slug rather than a lookup against
# a controlled vocabulary.
def canonicalize(value: str | None) -> str | None:
    if not value:
        return None
    decomposed = unicodedata.normalize("NFKD", value)
    stripped_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    slug = stripped_marks.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = re.sub(r"^-+|-+$", "", slug)
    return slug or None


YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


# First 4-digit year appearing in a title, if any -- the shared building
# block both sources' edition-year logic is built on.
def title_year(title: str) -> int | None:
    match = YEAR_RE.search(title)
    return int(match.group(0)) if match else None


# Fallback series-name guess for content with no dedicated series
# relationship/field: only fires when the title ends in a bare year
# ("<Name> <YYYY>"), verified against real Drupal report_flagship and
# DSpace data to produce zero false positives (e.g. "The Emissions Gap
# Report 2012" -> "Emissions Gap Report"; "GEO-5 for youth" / "Making Peace
# With Nature" correctly get no match). This is title-text inference, not
# an editorial signal -- callers should record the source as lower-
# confidence than a real resolved series field/term.
SERIES_FROM_TITLE_RE = re.compile(r"^(.*?)\s+((?:19|20)\d{2})(?:[/-]\d{2,4})?\s*$")


def infer_series_name_from_title(title: str) -> str | None:
    match = SERIES_FROM_TITLE_RE.match(title)
    if not match:
        return None
    name = re.sub(r"^(the)\s+", "", (match.group(1) or "").strip(), flags=re.IGNORECASE).strip()
    return name or None
