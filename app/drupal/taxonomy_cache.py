"""Port of ingestor/src/drupal/taxonomy-cache.ts."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import config
from app.drupal.client import DrupalClient, next_link, taxonomy_vocabulary
from app.types import JsonApiResource, JsonApiResourceIdentifier, TaxonomyValue

logger = logging.getLogger("taxonomy")


def _cache_key(type_: str, id_: str) -> str:
    return f"{type_}:{id_}"


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):  # bool is a subclass of int in Python -- exclude it
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _string_or_none(value: Any) -> str | None:
    # Drupal taxonomy term names occasionally carry stray leading/trailing
    # whitespace (e.g. " Global Environment Outlook") -- trim so it doesn't
    # leak into series_name/content_type/topics_name/etc. downstream.
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _taxonomy_value(resource: JsonApiResource) -> TaxonomyValue:
    attributes = resource.get("attributes") or {}
    return {
        "uuid": resource["id"],
        "tid": _number_or_none(attributes.get("drupal_internal__tid")),
        "resource_type": resource["type"],
        "vocabulary": taxonomy_vocabulary(resource["type"]),
        "name": _string_or_none(attributes.get("name")),
        "language": _string_or_none(attributes.get("langcode")),
    }


class TaxonomyCache:
    def __init__(self, drupal: DrupalClient) -> None:
        self._drupal = drupal
        self._cache: dict[str, TaxonomyValue] = {}
        self._preload_task: asyncio.Task[None] | None = None

    def preload_configured(self) -> asyncio.Task[None]:
        if self._preload_task is None:
            self._preload_task = asyncio.ensure_future(
                self._preload_all(config.drupal.taxonomy_resource_types)
            )
        return self._preload_task

    async def resolve(
        self, reference: JsonApiResourceIdentifier | None
    ) -> TaxonomyValue | None:
        if not reference:
            return None
        if not reference["type"].startswith("taxonomy_term--"):
            return None

        key = _cache_key(reference["type"], reference["id"])
        cached = self._cache.get(key)
        if cached:
            return cached

        resource = await self._drupal.get_resource(
            self._drupal.taxonomy_item_url(reference["type"], reference["id"])
        )
        value = _taxonomy_value(resource)
        self._cache[key] = value
        return value

    async def resolve_many(
        self, references: list[JsonApiResourceIdentifier] | None
    ) -> list[TaxonomyValue]:
        if not references:
            return []
        values = await asyncio.gather(*(self.resolve(ref) for ref in references))
        return [value for value in values if value is not None]

    def find_term_by_name(self, resource_type: str, name: str) -> TaxonomyValue | None:
        target = name.strip().lower()
        if not target:
            return None

        for value in self._cache.values():
            value_name = value["name"]
            if value["resource_type"] == resource_type and value_name is not None and value_name.lower() == target:
                return value
        return None

    async def _preload_all(self, resource_types: list[str]) -> None:
        for resource_type in resource_types:
            if not resource_type.startswith("taxonomy_term--"):
                continue
            await self._preload_resource_type(resource_type)

    async def _preload_resource_type(self, resource_type: str) -> None:
        url: str | None = self._drupal.taxonomy_collection_url(resource_type)

        while url:
            response = await self._drupal.get_collection(url)
            for resource in response["data"]:
                value = _taxonomy_value(resource)
                self._cache[_cache_key(resource["type"], resource["id"])] = value
            url = next_link(response.get("links"))

        logger.info(
            "[taxonomy] cached %s; total cached terms=%d", resource_type, len(self._cache)
        )
