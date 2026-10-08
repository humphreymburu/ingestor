"""Port of ingestor/src/drupal/mappers/event.ts.

Field names confirmed against a live node--event sample:
- field_content preferred over field_body, same convention as news.
- Start-date field is inconsistent across records: field_event_date is
  populated on older content, field_start_date on newer; take whichever is
  set. field_end_date_ (trailing underscore) looks like an abandoned/
  duplicate field on the Drupal side and is ignored.
- field_sdgs (not field_sdg_tags as in news); field_country resolves
  against taxonomy_term--un_environment_countries here, not the
  "countries" vocabulary news uses.
- event has no field_series relationship (news does).
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
    select_summary,
    string_value,
)
from app.types import DrupalRawDocument, JsonApiResource, TaxonomyResolver


async def map_event(
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
    sdgs = await resolve_many(taxonomies, relationships.get("field_sdgs"))
    countries = await resolve_many(taxonomies, relationships.get("field_country"))
    regions = await resolve_many(taxonomies, relationships.get("field_region"))

    body = select_body(attributes)
    path_alias = extract_path_alias(attributes.get("path"))
    event_date = string_value(attributes.get("field_event_date")) or string_value(
        attributes.get("field_start_date")
    )
    end_date = string_value(attributes.get("field_end_date"))

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
        "summary": select_summary(attributes),
        "author": string_value(attributes.get("field_author")),
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
        "sdgs": sdgs,
        "countries": countries,
        "regions": regions,
        "topics_name": names(topics),
        "tags_name": names(tags),
        "sdg_tags_name": names(sdgs),
        "country_name": names(countries),
        "region_name": names(regions),
        "created": string_value(attributes.get("created")),
        "changed": string_value(attributes.get("changed")),
        "published_at": string_value(attributes.get("published_at"))
        or string_value(attributes.get("created")),
        "event_date": event_date,
        "field_end_date": end_date,
        "path_alias": path_alias,
        "public_url": f"{config.public_base_url}{path_alias}" if path_alias else None,
        "ingest_run_id": run_id,
        "ingested_at": now_iso(),
    }
