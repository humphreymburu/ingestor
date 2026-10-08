"""Port of ingestor/src/types.ts.

JSON:API / DSpace HAL shapes are typed loosely (plain dicts + TypedDict
where it helps readability) rather than as strict models, since these are
externally-supplied payloads this service only reads selected fields from --
mirrors the TS interfaces' own permissiveness (`attributes?: Record<string,
any>`).

`RawDocumentCore`/`DrupalRawDocument`/`DspaceRawDocument` are documented here
as TypedDicts for reference, but mapper functions build and return plain
dicts -- the field names/types must stay a byte-for-byte match to the
`knowledge-raw-v2` mapping (`app/opensearch/index.py`); a TypedDict doesn't
enforce that at runtime, OpenSearch's `dynamic:"strict"` mapping does.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypedDict


class JsonApiResourceIdentifier(TypedDict, total=False):
    type: str
    id: str
    meta: dict[str, Any]


class JsonApiRelationship(TypedDict, total=False):
    data: JsonApiResourceIdentifier | list[JsonApiResourceIdentifier] | None
    links: dict[str, Any]


class JsonApiResource(TypedDict, total=False):
    type: str
    id: str
    attributes: dict[str, Any]
    relationships: dict[str, JsonApiRelationship]
    links: dict[str, Any]


class JsonApiCollectionResponse(TypedDict, total=False):
    data: list[JsonApiResource]
    included: list[JsonApiResource]
    links: dict[str, Any]


class TaxonomyValue(TypedDict):
    uuid: str
    tid: int | None
    resource_type: str
    vocabulary: str
    name: str | None
    language: str | None


class TaxonomyResolver(Protocol):
    """Structural typing seam so mapper functions can take a fake in tests
    instead of a live TaxonomyCache."""

    async def resolve(
        self, reference: JsonApiResourceIdentifier | None
    ) -> TaxonomyValue | None: ...

    async def resolve_many(
        self, references: list[JsonApiResourceIdentifier] | None
    ) -> list[TaxonomyValue]: ...

    def find_term_by_name(self, resource_type: str, name: str) -> TaxonomyValue | None: ...


# Field names/types here are the exact contract of the `knowledge-raw-v2`
# index mapping (dynamic:"strict" -- any field not declared there gets the
# whole document rejected, and an existing field's type can never be changed
# via a mapping update, only by recreating the index).
class RawDocumentCore(TypedDict):
    source_id: str
    parent_id: str
    language: str
    title: str
    summary: str | None
    author: str | None
    content_type_raw: str | None
    content_type_canonical: str | None
    content_type_term: TaxonomyValue | None
    series_name: str | None
    series_canonical: str | None
    series_source: str | None
    series_term: TaxonomyValue | None
    edition_year: int | None
    edition_source: str | None
    published_at: str | None
    public_url: str | None
    content_hash: str | None
    ingest_run_id: str
    ingested_at: str


class DrupalRawDocument(RawDocumentCore):
    source: Literal["drupal"]
    uuid: str
    nid: int | None
    vid: int | None
    bundle: str
    status: bool
    moderation_state: str | None
    body_html: str | None
    body_format: str | None
    topics: list[TaxonomyValue]
    tags: list[TaxonomyValue]
    sdgs: list[TaxonomyValue]
    countries: list[TaxonomyValue]
    regions: list[TaxonomyValue]
    topics_name: list[str]
    tags_name: list[str]
    sdg_tags_name: list[str]
    country_name: list[str]
    region_name: list[str]
    created: str | None
    changed: str | None
    event_date: str | None
    field_end_date: str | None
    path_alias: str | None


class DspaceMetadataValue(TypedDict, total=False):
    value: str
    language: str | None
    authority: str | None
    confidence: int
    place: int


DspaceMetadata = dict[str, list[DspaceMetadataValue]]


class DspaceItem(TypedDict, total=False):
    id: str
    uuid: str
    name: str
    handle: str
    metadata: DspaceMetadata
    inArchive: bool
    discoverable: bool
    withdrawn: bool
    lastModified: str
    entityType: str | None
    type: Literal["item"]
    _links: dict[str, Any]


class DspaceBundle(TypedDict, total=False):
    uuid: str
    name: str
    metadata: DspaceMetadata
    type: Literal["bundle"]
    _links: dict[str, Any]


class DspaceCheckSum(TypedDict, total=False):
    checkSumAlgorithm: str
    value: str


class DspaceBitstream(TypedDict, total=False):
    id: str
    uuid: str
    name: str
    metadata: DspaceMetadata
    bundleName: str
    sizeBytes: int
    checkSum: DspaceCheckSum
    sequenceId: int
    type: Literal["bitstream"]
    _links: dict[str, Any]


class DspacePage(TypedDict, total=False):
    number: int
    size: int
    totalPages: int
    totalElements: int


class DspaceRawDocument(RawDocumentCore):
    source: Literal["dspace"]
    fulltext: str | None
    fulltext_chars: int | None
    image_url: str | None
    byte_hash: str | None
    dspace: dict[str, Any]
    bitstream: dict[str, Any]


RawDocument = DrupalRawDocument | DspaceRawDocument
