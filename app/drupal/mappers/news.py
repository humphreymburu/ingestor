"""Port of ingestor/src/drupal/mappers/news.ts."""

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


async def map_news(
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
    series = await resolve_one(taxonomies, relationships.get("field_series"))
    topics = await resolve_many(taxonomies, relationships.get("field_primary_topics"))
    tags = await resolve_many(taxonomies, relationships.get("field_tags"))
    sdgs = await resolve_many(taxonomies, relationships.get("field_sdg_tags"))
    countries = await resolve_many(taxonomies, relationships.get("field_country"))
    regions = await resolve_many(taxonomies, relationships.get("field_region"))

    body = select_body(attributes)
    path_alias = extract_path_alias(attributes.get("path"))

    return {
        "source": "drupal",
        # Drupal entity UUID may be shared by translations, so language is
        # part of the raw document ID.
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
        "series_name": series["name"] if series else None,
        "series_canonical": canonicalize(series["name"] if series else None),
        "series_source": "field_series" if series else None,
        "series_term": series,
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
        "event_date": None,
        "field_end_date": None,
        "path_alias": path_alias,
        "public_url": f"{config.public_base_url}{path_alias}" if path_alias else None,
        "ingest_run_id": run_id,
        "ingested_at": now_iso(),
    }
