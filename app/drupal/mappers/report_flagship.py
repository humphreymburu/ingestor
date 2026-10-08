"""Port of ingestor/src/drupal/mappers/report-flagship.ts.

Field names confirmed against live node--report_flagship samples ("Global
Environment Outlook 6"/"5"): field_content_type/field_primary_topics/
field_tags reuse the news names, but:
- field_sdgs (not field_sdg_tags, matches event's naming) -- there is no
  field_country or field_region relationship on this bundle at all.
- field_authors is a plain array of free-text strings (e.g.
  ["UN Environment"]), not an entity reference -- joined into `author`.
- field_flagship_series (-> taxonomy_term--flagship_landing) is this
  bundle's equivalent of news' field_series -> mapped into the shared
  `series` slot. It's null on roughly half of live records even for content
  that's clearly part of a series -- when absent, try in order: (1) a
  field_tags entry whose name exactly matches a real flagship_landing term
  (resolve_series_from_tags), which still resolves to a real term, then (2)
  a title-derived guess (infer_series_name_from_title) which doesn't;
  series_source records which one produced it, and the `series` object
  field itself only ever holds a real resolved taxonomy term (tiers 1-2),
  never the title guess (tier 3).
- no field_summary attribute; field_information (formatted text, a short
  editorial blurb distinct from the field_content body) is used as the
  summary source instead.
- no Drupal field carries a report's edition/year directly -- extracted
  from the title, falling back to published_at (see extract_edition).
"""

from __future__ import annotations

from app.config import config
from app.drupal.mappers.common import (
    canonicalize,
    content_hash,
    extract_edition,
    extract_path_alias,
    formatted_text,
    infer_series_name_from_title,
    join_strings,
    names,
    now_iso,
    number_value,
    resolve_many,
    resolve_one,
    resolve_series_from_tags,
    select_body,
    string_value,
)
from app.types import DrupalRawDocument, JsonApiResource, TaxonomyResolver

FLAGSHIP_SERIES_RESOURCE_TYPE = "taxonomy_term--flagship_landing"


async def map_report_flagship(
    resource: JsonApiResource, bundle: str, run_id: str, taxonomies: TaxonomyResolver
) -> DrupalRawDocument | None:
    attributes = resource.get("attributes") or {}
    relationships = resource.get("relationships") or {}

    status = attributes.get("status") is True
    if not status:
        return None

    language = string_value(attributes.get("langcode")) or "und"
    uuid = resource["id"]
    nid = number_value(attributes.get("drupal_internal__nid"))
    vid = number_value(attributes.get("drupal_internal__vid"))
    source_id = f"drupal:{uuid}:{language}"

    content_type_term = await resolve_one(taxonomies, relationships.get("field_content_type"))
    series_from_field = await resolve_one(taxonomies, relationships.get("field_flagship_series"))
    topics = await resolve_many(taxonomies, relationships.get("field_primary_topics"))
    tags = await resolve_many(taxonomies, relationships.get("field_tags"))
    sdgs = await resolve_many(taxonomies, relationships.get("field_sdgs"))

    body = select_body(attributes)
    path_alias = extract_path_alias(attributes.get("path"))
    title = string_value(attributes.get("title")) or ""
    published_at = string_value(attributes.get("published_at")) or string_value(
        attributes.get("created")
    )

    series_from_tags = (
        None
        if series_from_field
        else resolve_series_from_tags(taxonomies, tags, FLAGSHIP_SERIES_RESOURCE_TYPE)
    )
    series = series_from_field or series_from_tags

    inferred_series_name = None if series else infer_series_name_from_title(title)
    series_name = (series["name"] if series else None) or inferred_series_name
    series_source = (
        "field_flagship_series"
        if series_from_field
        else "field_tags"
        if series_from_tags
        else "title_inferred"
        if inferred_series_name
        else None
    )

    edition = extract_edition(title, published_at)

    return {
        "source": "drupal",
        "source_id": source_id,
        "parent_id": f"drupal:{uuid}",
        "uuid": uuid,
        "nid": nid,
        "vid": vid,
        "bundle": bundle,
        "language": language,
        "status": status,
        "moderation_state": string_value(attributes.get("moderation_state")),
        "title": title,
        "body_html": body["value"],
        "content_hash": content_hash(body["value"]),
        "body_format": body["format"],
        "summary": formatted_text(attributes.get("field_information"))["value"],
        "author": join_strings(attributes.get("field_authors")),
        "content_type_raw": content_type_term["name"] if content_type_term else None,
        "content_type_canonical": canonicalize(
            content_type_term["name"] if content_type_term else None
        ),
        "content_type_term": content_type_term,
        "series_name": series_name,
        "series_canonical": canonicalize(series_name),
        "series_source": series_source,
        "series_term": series,
        "edition_year": edition["edition"],
        "edition_source": edition["source"],
        "topics": topics,
        "tags": tags,
        "sdgs": sdgs,
        "countries": [],
        "regions": [],
        "topics_name": names(topics),
        "tags_name": names(tags),
        "sdg_tags_name": names(sdgs),
        "country_name": [],
        "region_name": [],
        "created": string_value(attributes.get("created")),
        "changed": string_value(attributes.get("changed")),
        "published_at": published_at,
        "event_date": None,
        "field_end_date": None,
        "path_alias": path_alias,
        "public_url": f"{config.public_base_url}{path_alias}" if path_alias else None,
        "ingest_run_id": run_id,
        "ingested_at": now_iso(),
    }
