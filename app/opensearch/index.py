"""Port of ingestor/src/opensearch/index.ts.

Documents are passed through as plain dicts (produced by app/drupal/mapper.py
/ app/dspace/mapper.py) rather than a rigid model here, since the index's
`dynamic: "strict"` mapping is itself the authoritative field contract --
enforced by OpenSearch at write time, not re-validated client-side.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opensearchpy import OpenSearch

from app.config import config

logger = logging.getLogger("opensearch.index")

# Shared sub-mapping for every TaxonomyValue-shaped field (content_type_term,
# series_term, topics, tags, sdgs, countries, regions) -- keeps all seven in
# lockstep with the TaxonomyValue shape in app/types.py.
TAXONOMY_VALUE_PROPERTIES: dict[str, Any] = {
    "uuid": {"type": "keyword"},
    "tid": {"type": "integer"},
    "resource_type": {"type": "keyword"},
    "vocabulary": {"type": "keyword"},
    "name": {
        "type": "text",
        "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}},
    },
    "language": {"type": "keyword"},
}


class RawIndex:
    def __init__(self, client: OpenSearch) -> None:
        self._client = client

    def ensure_index(self) -> None:
        if self._client.indices.exists(index=config.opensearch.index):
            return

        self._client.indices.create(
            index=config.opensearch.index,
            body={
                "settings": {
                    "number_of_shards": 5,
                    "number_of_replicas": 1,
                },
                "mappings": {
                    # dynamic:"strict" -- unlike the shared legacy
                    # `knowledge-raw` index (dc_*/wd_*/unepmap_*/
                    # dspace_entity_* fields from other pipelines), this v2
                    # mapping is the full authoritative contract: any field
                    # not declared here gets the WHOLE document rejected on
                    # write. This block only runs when the index doesn't
                    # exist yet (see ensure_index above); an existing
                    # field's type can never be changed via a mapping
                    # update, only by recreating the index.
                    "dynamic": "strict",
                    "properties": {
                        "source": {"type": "keyword"},
                        "source_id": {"type": "keyword"},
                        "parent_id": {"type": "keyword"},
                        "language": {"type": "keyword"},
                        "title": {
                            "type": "text",
                            "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}},
                        },
                        "summary": {"type": "text"},
                        "author": {"type": "keyword", "ignore_above": 1024},
                        "content_type_raw": {"type": "keyword"},
                        "content_type_canonical": {"type": "keyword"},
                        "content_type_term": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "series_name": {"type": "keyword", "ignore_above": 1024},
                        "series_canonical": {"type": "keyword"},
                        "series_source": {"type": "keyword"},
                        "series_term": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "edition_year": {"type": "integer"},
                        "edition_source": {"type": "keyword"},
                        "published_at": {"type": "date"},
                        "public_url": {"type": "keyword", "ignore_above": 2048},
                        "content_hash": {"type": "keyword"},
                        "ingest_run_id": {"type": "keyword"},
                        "ingested_at": {"type": "date"},
                        # Drupal-specific.
                        "uuid": {"type": "keyword"},
                        "nid": {"type": "integer"},
                        "vid": {"type": "long"},
                        "bundle": {"type": "keyword"},
                        "status": {"type": "boolean"},
                        "moderation_state": {"type": "keyword"},
                        "body_html": {"type": "text", "index": False},
                        "body_format": {"type": "keyword"},
                        "topics": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "topics_name": {"type": "keyword", "ignore_above": 1024},
                        "tags": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "tags_name": {"type": "keyword", "ignore_above": 1024},
                        "sdgs": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "sdg_tags_name": {"type": "keyword", "ignore_above": 1024},
                        "countries": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "country_name": {"type": "keyword", "ignore_above": 1024},
                        "regions": {"properties": TAXONOMY_VALUE_PROPERTIES},
                        "region_name": {"type": "keyword", "ignore_above": 1024},
                        "created": {"type": "date"},
                        "changed": {"type": "date"},
                        "event_date": {"type": "date"},
                        "field_end_date": {"type": "date"},
                        "path_alias": {"type": "keyword", "ignore_above": 2048},
                        # DSpace-specific (provisioned ahead of the adapter
                        # being queue-wired; see app/types.py).
                        "fulltext": {"type": "text", "index": False},
                        "fulltext_chars": {"type": "long"},
                        # DSpace auto-generates one thumbnail JPEG per
                        # ORIGINAL bitstream. Not full-text searched, just
                        # stored for display.
                        "image_url": {"type": "keyword", "index": False, "ignore_above": 2048},
                        # sha256 of the raw PDF bytes -- indexed (not
                        # index:false) so find_by_byte_hash() can query it
                        # for exact-duplicate detection.
                        "byte_hash": {"type": "keyword"},
                        "dspace": {
                            "properties": {
                                "item_uuid": {"type": "keyword"},
                                "handle": {"type": "keyword"},
                                "last_modified": {"type": "date"},
                                "date_accessioned": {"type": "date"},
                                "in_archive": {"type": "boolean"},
                                "discoverable": {"type": "boolean"},
                                "withdrawn": {"type": "boolean"},
                            },
                        },
                        "bitstream": {
                            "properties": {
                                "uuid": {"type": "keyword"},
                                "bundle": {"type": "keyword"},
                                "filename": {"type": "keyword", "ignore_above": 2048},
                                "mime_type": {"type": "keyword"},
                                "size_bytes": {"type": "long"},
                                "checksum_algorithm": {"type": "keyword"},
                                "checksum": {"type": "keyword"},
                            },
                        },
                    },
                },
            },
        )

    def bulk_index(self, documents: list[dict[str, Any]]) -> None:
        if not documents:
            return

        body: list[dict[str, Any]] = []
        for document in documents:
            body.append({"index": {"_index": config.opensearch.index, "_id": document["source_id"]}})
            body.append(document)

        result = self._client.bulk(body=body, refresh=False)
        items = result.get("items") or []

        # One line per document, success or failure, regardless of caller
        # (production queue workers and the ad-hoc CLI both go through
        # here). "bundle" here means whatever groups this doc within its
        # source -- Drupal's content-type bundle, or DSpace's bitstream
        # bundle name (always "ORIGINAL" for anything mapDspaceBitstream
        # ever produces, but kept from the doc rather than hardcoded in
        # case that changes).
        for i, document in enumerate(documents):
            bundle = document["bundle"] if document["source"] == "drupal" else document["bitstream"]["bundle"]
            error = ((items[i] or {}).get("index") or {}).get("error") if i < len(items) else None
            if error:
                logger.error(
                    "[index] ✗ %s:%s %s %r: %s",
                    document["source"],
                    bundle,
                    document["source_id"],
                    document["title"],
                    json.dumps(error)[:300],
                )
            else:
                logger.info(
                    "[index] ✓ %s:%s %s %r",
                    document["source"],
                    bundle,
                    document["source_id"],
                    document["title"],
                )

        if not result.get("errors"):
            return

        failures = [item["index"] for item in items if item.get("index", {}).get("error")]
        raise RuntimeError(
            f"OpenSearch bulk indexing failed for {len(failures)}/{len(documents)} documents: "
            f"{json.dumps(failures[:5])}"
        )

    def reconcile_bundle(self, bundle: str, run_id: str) -> dict[str, Any]:
        """Deletes/unpublishes are invisible to `changed`-cursor incremental
        sync (Drupal just stops returning the node, it doesn't send a
        tombstone), so a raw doc for deleted/unpublished content can linger
        indefinitely. This closes that gap for a FULL run: every doc a run
        writes carries that run's `ingest_run_id`, so once the run has
        walked every page for a bundle, anything of that bundle+language
        still tagged with an older run id was not reaffirmed by this run
        and is stale -- delete it.

        Callers MUST only invoke this after a genuinely complete, unbounded
        full-mode run for the bundle. Never call it after an incremental
        run or a capped/partial one -- "not seen in this run" would then
        just mean "wasn't in scope", not "gone", and this would delete
        perfectly live content.
        """
        # Scope the delete to only the languages this run actually touched
        # for this bundle -- derived from the index itself, not assumed, so
        # a single-language run never touches other languages' documents.
        agg = self._client.search(
            index=config.opensearch.index,
            body={
                "size": 0,
                "query": {
                    "bool": {
                        "filter": [{"term": {"bundle": bundle}}, {"term": {"ingest_run_id": run_id}}]
                    }
                },
                "aggs": {"languages": {"terms": {"field": "language", "size": 100}}},
            },
        )

        buckets = ((agg.get("aggregations") or {}).get("languages") or {}).get("buckets") or []
        languages = [bucket["key"] for bucket in buckets]

        # This run wrote nothing for this bundle -- abstain rather than
        # delete under an empty/unknown scope (e.g. a full run that failed
        # before indexing anything must never be treated as "bundle is now
        # empty").
        if not languages:
            return {"deleted": 0, "languages": []}

        response = self._client.delete_by_query(
            index=config.opensearch.index,
            refresh=True,
            body={
                "query": {
                    "bool": {
                        "filter": [{"term": {"bundle": bundle}}, {"terms": {"language": languages}}],
                        "must_not": [{"term": {"ingest_run_id": run_id}}],
                    }
                }
            },
        )

        return {"deleted": response.get("deleted", 0), "languages": languages}

    def find_by_byte_hash(self, byte_hash: str, exclude_source_id: str | None = None) -> list[str]:
        """Stage-1 near-duplicate detection only -- exact byte match. Returns
        every OTHER document's source_id already indexed with this
        byte_hash, so a caller can log/flag it. No merge/delete action taken
        here -- that needs a real dedup policy, not built; this only
        surfaces the finding.
        """
        response = self._client.search(
            index=config.opensearch.index,
            body={
                "size": 50,
                "query": {"term": {"byte_hash": byte_hash}},
                "_source": ["source_id"],
            },
        )

        hits = ((response.get("hits") or {}).get("hits")) or []
        ids = [hit["_source"]["source_id"] for hit in hits]
        return [id_ for id_ in ids if id_ != exclude_source_id]
