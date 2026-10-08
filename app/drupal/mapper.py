"""Port of ingestor/src/drupal/mapper.ts."""

from __future__ import annotations

from app.drupal.mappers.registry import BUNDLE_MAPPERS
from app.types import DrupalRawDocument, JsonApiResource, TaxonomyResolver


class DrupalMapper:
    def __init__(self, taxonomies: TaxonomyResolver) -> None:
        self._taxonomies = taxonomies

    async def map(
        self, resource: JsonApiResource, bundle: str, run_id: str
    ) -> DrupalRawDocument | None:
        map_fn = BUNDLE_MAPPERS.get(bundle)
        if map_fn is None:
            raise RuntimeError(
                f'No mapper registered for Drupal bundle "{bundle}". Add one in '
                "app/drupal/mappers/ and register it in registry.py before enabling "
                "it in DRUPAL_BUNDLES."
            )
        return await map_fn(resource, bundle, run_id, self._taxonomies)
