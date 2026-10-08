"""Port of ingestor/src/drupal/mappers/common.ts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from app.shared.text import canonicalize, content_hash, infer_series_name_from_title, title_year
from app.types import JsonApiRelationship, JsonApiResourceIdentifier, TaxonomyResolver, TaxonomyValue

__all__ = [
    "canonicalize",
    "content_hash",
    "infer_series_name_from_title",
    "resolve_one",
    "resolve_many",
    "resolve_series_from_tags",
    "select_body",
    "select_summary",
    "formatted_text",
    "extract_path_alias",
    "names",
    "string_value",
    "join_strings",
    "number_value",
    "extract_edition",
    "now_iso",
]


def now_iso() -> str:
    """Mirrors `new Date().toISOString()`: UTC, millisecond precision, "Z" suffix."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def resolve_one(
    taxonomies: TaxonomyResolver, relationship: JsonApiRelationship | None
) -> TaxonomyValue | None:
    ref = (relationship or {}).get("data")
    if not ref or isinstance(ref, list):
        return None
    return await taxonomies.resolve(ref)


async def resolve_many(
    taxonomies: TaxonomyResolver, relationship: JsonApiRelationship | None
) -> list[TaxonomyValue]:
    refs = (relationship or {}).get("data")
    if not isinstance(refs, list):
        return []
    return await taxonomies.resolve_many(refs)


# Fallback series signal for when a bundle has no dedicated series
# relationship set: if any of the content's free-tags exactly matches (by
# name) a real term in a series vocabulary, use that term. Exact name match
# against a real controlled vocabulary, so meaningfully safer than free-text
# title parsing -- prefer this over infer_series_name_from_title when both
# could apply.
#
# Requires the series vocabulary to already be preloaded on `taxonomies`
# (DRUPAL_TAXONOMY_RESOURCE_TYPES / preload_configured()) -- find_term_by_name
# only scans what's cached, it doesn't fetch.
def resolve_series_from_tags(
    taxonomies: TaxonomyResolver, tags: list[TaxonomyValue], series_resource_type: str
) -> TaxonomyValue | None:
    for tag in tags:
        if not tag["name"]:
            continue
        match = taxonomies.find_term_by_name(series_resource_type, tag["name"])
        if match:
            return match
    return None


# Do not concatenate field_content + field_body. They can represent
# overlapping editorial bodies; field_content is preferred where populated.
def select_body(attributes: dict[str, Any]) -> dict[str, str | None]:
    preferred = formatted_text(attributes.get("field_content"))
    if preferred["value"]:
        return preferred
    return formatted_text(attributes.get("field_body"))


def select_summary(attributes: dict[str, Any]) -> str | None:
    explicit = string_value(attributes.get("field_summary"))
    if explicit:
        return explicit

    body_summary = (attributes.get("field_body") or {})
    if not isinstance(body_summary, dict):
        return None
    return string_value(body_summary.get("summary"))


def formatted_text(value: Any) -> dict[str, str | None]:
    if not value or not isinstance(value, dict):
        return {"value": None, "format": None}
    # Store original editorial value, not processed output; downstream
    # normalization owns HTML cleanup.
    return {"value": string_value(value.get("value")), "format": string_value(value.get("format"))}


def extract_path_alias(path: Any) -> str | None:
    if not path or not isinstance(path, dict):
        return None
    return string_value(path.get("alias"))


def names(values: list[TaxonomyValue]) -> list[str]:
    return [value["name"] for value in values if value["name"]]


def string_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() != "" else None


# field_authors (report_flagship, and possibly other bundles) is a plain
# array of free-text strings, not an entity reference -- distinct from the
# taxonomy-resolved topics/tags/etc. arrays.
def join_strings(value: Any, separator: str = ", ") -> str | None:
    if not isinstance(value, list):
        return string_value(value)
    items = [item for item in value if isinstance(item, str) and item.strip() != ""]
    return separator.join(items) if items else None


def number_value(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


# Report edition (e.g. "Emissions Gap Report 2012" -> 2012). No Drupal field
# carries this directly, so: prefer a year appearing in the title (the
# editorial signal -- what the publication itself is called), falling back
# to the publish year when the title has none. Deliberately scoped to
# report_flagship's mapper only -- applying "first year-looking number in
# the title" to a news headline would produce nonsense editions.
def extract_edition(
    title: str, published_at: str | None
) -> dict[str, int | Literal["title", "published_at"] | None]:
    from_title = title_year(title)
    if from_title:
        return {"edition": from_title, "source": "title"}

    if published_at:
        year = _year_from_iso(published_at)
        if year is not None:
            return {"edition": year, "source": "published_at"}

    return {"edition": None, "source": None}


def _year_from_iso(value: str) -> int | None:
    """Best-effort year extraction mirroring JS's lenient
    `new Date(value).getFullYear()` -- ISO 8601 strings (what published_at/
    created actually carry) parse fine via fromisoformat; anything else is
    treated the same as JS's `Invalid Date` (NaN year -> None)."""
    from datetime import datetime

    try:
        # Python's fromisoformat doesn't accept a trailing "Z" before 3.11;
        # normalize it the same way most of this codebase's ISO timestamps
        # already come formatted (+00:00), for forward/back compatibility.
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(normalized).year
    except ValueError:
        return None
