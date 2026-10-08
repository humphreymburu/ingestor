"""Port of ingestor/src/dspace/series.ts."""

from __future__ import annotations

import re

from app.shared.text import canonicalize, infer_series_name_from_title, title_year
from app.types import DspaceItem


def _first_metadata_value(item: DspaceItem, field: str) -> str | None:
    values = item.get("metadata", {}).get(field)
    if not values:
        return None
    raw = values[0].get("value")
    raw = raw.strip() if raw else None
    return raw or None


# dc.relation.ispartof is the real series signal on this instance --
# confirmed live: "Emissions Gap Report 2025 - Executive Summary" carries
# dc.relation.ispartof = "Emissions Gap Report" exactly. Falls back to the
# same title-ends-in-a-year heuristic Drupal's report_flagship mapper uses
# when the field is absent.
def resolve_series(item: DspaceItem, title: str) -> dict[str, str | None]:
    from_field = _first_metadata_value(item, "dc.relation.ispartof")
    if from_field:
        return {
            "name": from_field,
            "canonical": canonicalize(from_field),
            "source": "dspace:dc.relation.ispartof",
        }

    inferred = infer_series_name_from_title(title)
    if inferred:
        return {"name": inferred, "canonical": canonicalize(inferred), "source": "title_inferred"}

    return {"name": None, "canonical": None, "source": None}


# Deliberately conservative: edition_year is only ever populated for
# content with an established series (real or title-inferred) -- never for
# an arbitrary standalone document ("ordinary document dated 2025" must not
# become edition_year=2025 just because it was published in 2025). A title
# year is trusted outright; a series with no title year falls back to
# dc.date.issued's year, since a report cover year usually tracks its issue
# date once you already know it's part of a series.
def resolve_edition_year(
    title: str, dc_date_issued: str | None, has_series: bool
) -> dict[str, int | str | None]:
    from_title = title_year(title)
    if from_title:
        return {"year": from_title, "source": "title"}

    if has_series and dc_date_issued:
        year = _year_from_partial_date(dc_date_issued)
        if year is not None:
            return {"year": year, "source": "dspace:dc.date.issued"}

    return {"year": None, "source": None}


def _year_from_partial_date(value: str) -> int | None:
    """Mirrors JS's lenient `new Date(value).getFullYear()` for DSpace's
    often-partial dc.date.issued values ("2025", "2025-11", full ISO
    timestamps) -- a leading 4-digit year is all that's needed here."""
    match = re.match(r"^(\d{4})", value)
    return int(match.group(1)) if match else None
