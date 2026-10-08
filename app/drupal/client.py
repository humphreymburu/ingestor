"""Port of ingestor/src/drupal/client.ts."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import config
from app.types import JsonApiCollectionResponse, JsonApiResource


def taxonomy_vocabulary(resource_type: str) -> str:
    prefix = "taxonomy_term--"
    if not resource_type.startswith(prefix):
        raise ValueError(f"Not a taxonomy JSON:API resource type: {resource_type}")
    return resource_type[len(prefix) :]


def next_link(links: dict[str, Any] | None) -> str | None:
    next_value = (links or {}).get("next")
    if not next_value:
        return None
    return next_value if isinstance(next_value, str) else next_value["href"]


class DrupalClient:
    def __init__(self) -> None:
        self._headers: dict[str, str] = {"Accept": "application/vnd.api+json"}
        # One client for the DrupalClient's lifetime (mirrors this class
        # being instantiated once per process in main.py/cli.py) rather
        # than opening a fresh connection per request.
        self._http = httpx.AsyncClient(timeout=config.drupal.timeout_ms / 1000)

        if config.drupal.bearer_token:
            self._headers["Authorization"] = f"Bearer {config.drupal.bearer_token}"
        elif config.drupal.basic_user and config.drupal.basic_password:
            token = base64.b64encode(
                f"{config.drupal.basic_user}:{config.drupal.basic_password}".encode()
            ).decode()
            self._headers["Authorization"] = f"Basic {token}"

    def collection_url(self, bundle: str, changed_after: str | None = None) -> str:
        params: dict[str, str] = {
            "page[limit]": str(config.drupal.page_limit),
            "filter[status]": "1",
            "sort": "changed",
        }
        if changed_after:
            params["filter[changed-after][condition][path]"] = "changed"
            params["filter[changed-after][condition][operator]"] = ">"
            params["filter[changed-after][condition][value]"] = changed_after

        return f"{config.drupal.api_base}/node/{bundle}?{urlencode(params)}"

    def taxonomy_collection_url(self, resource_type: str) -> str:
        vocabulary = taxonomy_vocabulary(resource_type)
        params = {
            "page[limit]": str(config.drupal.page_limit),
            f"fields[{resource_type}]": "drupal_internal__tid,langcode,name,status,changed",
        }
        return f"{config.drupal.api_base}/taxonomy_term/{vocabulary}?{urlencode(params)}"

    def taxonomy_item_url(self, resource_type: str, uuid: str) -> str:
        vocabulary = taxonomy_vocabulary(resource_type)
        params = {f"fields[{resource_type}]": "drupal_internal__tid,langcode,name,status,changed"}
        return (
            f"{config.drupal.api_base}/taxonomy_term/{vocabulary}/{uuid}"
            f"?{urlencode(params)}"
        )

    async def get_collection(self, url: str) -> JsonApiCollectionResponse:
        return await self._get_json(url)

    async def get_resource(self, url: str) -> JsonApiResource:
        response: dict[str, Any] = await self._get_json(url)
        return response["data"]

    async def _get_json(self, url: str) -> Any:
        response = await self._http.get(url, headers=self._headers)

        if response.is_error:
            body = response.text
            raise RuntimeError(
                f"Drupal {response.status_code} {response.reason_phrase} for {url}: {body[:500]}"
            )

        return response.json()

    async def close(self) -> None:
        await self._http.aclose()
