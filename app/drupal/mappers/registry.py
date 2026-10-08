"""Port of ingestor/src/drupal/mappers/registry.ts.

Register a bundle here only once its mapper has been written and verified
against real Drupal attributes/relationships for that bundle. A bundle left
unregistered fails loudly in DrupalMapper.map() rather than being run
through another bundle's shape and indexing wrong data.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.drupal.mappers.event import map_event
from app.drupal.mappers.news import map_news
from app.drupal.mappers.page import map_page
from app.drupal.mappers.report_flagship import map_report_flagship
from app.types import DrupalRawDocument, JsonApiResource, TaxonomyResolver

BundleMapperFn = Callable[
    [JsonApiResource, str, str, TaxonomyResolver], Awaitable[DrupalRawDocument | None]
]

BUNDLE_MAPPERS: dict[str, BundleMapperFn] = {
    "news": map_news,
    "event": map_event,
    "page": map_page,
    "report_flagship": map_report_flagship,
}
