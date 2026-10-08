"""Port of ingestor/src/drupal/mappers/page.ts.

Field names confirmed against live node--page samples ("Error 404"/"Error
403" pages): field_content_type/field_primary_topics/field_tags/
field_country/field_region reuse the same names as news, but:
- no field_summary or field_author attribute at all on this bundle;
  field_description (plain string, not formatted text) is the closest
  thing to a summary and is used as a fallback.
- no field_series and no SDG relationship of any kind.
- field_event_date/field_end_date exist on this bundle despite it being the
  generic "Basic page" type (shared field group with project/campaign-style
  pages) -- populated when present, null otherwise.
"""

from __future__ import annotations

from app.config import config
from app.drupal.mappers.common import (
    canonicalize,
    content_hash,
    extract_path_alias,
    names,
    now_iso,
    number_value,
    resolve_many,
    resolve_one,
    select_body,
    string_value,
)
from app.types import DrupalRawDocument, JsonApiResource, TaxonomyResolver


async def map_page(
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
    topics = await resolve_many(taxonomies, relationships.get("field_primary_topics"))
    tags = await resolve_many(taxonomies, relationships.get("field_tags"))
    countries = await resolve_many(taxonomies, relationships.get("field_country"))
    regions = await resolve_many(taxonomies, relationships.get("field_region"))

    body = select_body(attributes)
    path_alias = extract_path_alias(attributes.get("path"))

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
        "title": string_value(attributes.get("title")) or "",
        "body_html": body["value"],
        "content_hash": content_hash(body["value"]),
        "body_format": body["format"],
        "summary": string_value(attributes.get("field_description")),
        "author": None,
        "content_type_raw": content_type_term["name"] if content_type_term else None,
        "content_type_canonical": canonicalize(
            content_type_term["name"] if content_type_term else None
        ),
        "content_type_term": content_type_term,
        "series_name": None,
        "series_canonical": None,
        "series_source": None,
        "series_term": None,
        "edition_year": None,
        "edition_source": None,
        "topics": topics,
        "tags": tags,
        "sdgs": [],
        "countries": countries,
        "regions": regions,
        "topics_name": names(topics),
        "tags_name": names(tags),
        "sdg_tags_name": [],
        "country_name": names(countries),
        "region_name": names(regions),
        "created": string_value(attributes.get("created")),
        "changed": string_value(attributes.get("changed")),
        "published_at": string_value(attributes.get("published_at"))
        or string_value(attributes.get("created")),
        "event_date": string_value(attributes.get("field_event_date")),
        "field_end_date": string_value(attributes.get("field_end_date")),
        "path_alias": path_alias,
        "public_url": f"{config.public_base_url}{path_alias}" if path_alias else None,
        "ingest_run_id": run_id,
        "ingested_at": now_iso(),
    }
