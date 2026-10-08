"""Port of ingestor/src/pdf/completeness.ts.

Completeness accounting, reduced to a two-state model since there's no
canonical block graph in the flat-fulltext path: every native span is
ASSIGNED (made it into the assembled fulltext) or INTENTIONALLY_EXCLUDED
(header/footer/page-number, see reconstruction.py) -- never silently
unaccounted for, by construction.

The point isn't the report object itself -- it's making "how much of this
page's real text made it into the index, and exactly why any of it didn't"
answerable by querying data instead of trusting that the extraction
"looked fine."
"""

from __future__ import annotations

from app.pdf.types import PageCompletenessReport, SourceSpan

# A WARN, not a hard failure -- an unusually high exclusion share is worth
# a human glance (real dense front-matter, or a false-positive header/
# footer match eating real body text) but isn't proof of either.
WARN_EXCLUDED_CHAR_SHARE = 0.4


def build_page_completeness_report(
    page_number: int, spans: list[SourceSpan]
) -> PageCompletenessReport:
    assigned = [s for s in spans if s["status"] == "ASSIGNED"]
    excluded = [s for s in spans if s["status"] == "INTENTIONALLY_EXCLUDED"]

    def char_count(items: list[SourceSpan]) -> int:
        return sum(len(s["text"]) for s in items)

    source_character_count = char_count(spans)
    assigned_character_count = char_count(assigned)
    excluded_character_count = char_count(excluded)

    excluded_reasons: dict[str, int] = {}
    for span in excluded:
        reason = span.get("exclusionReason")
        if not reason:
            continue
        excluded_reasons[reason] = excluded_reasons.get(reason, 0) + 1

    excluded_share = (
        excluded_character_count / source_character_count if source_character_count > 0 else 0
    )

    return {
        "pageNumber": page_number,
        "sourceSpanCount": len(spans),
        "sourceCharacterCount": source_character_count,
        "assignedSpanCount": len(assigned),
        "assignedCharacterCount": assigned_character_count,
        "excludedSpanCount": len(excluded),
        "excludedCharacterCount": excluded_character_count,
        "excludedReasons": excluded_reasons,
        "status": "WARN" if excluded_share > WARN_EXCLUDED_CHAR_SHARE else "PASS",
    }
