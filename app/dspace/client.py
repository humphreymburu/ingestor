"""Port of ingestor/src/dspace/client.ts.

Isolates DSpace's API-generation specifics (legacy vs DSpace 7+ HAL) behind
one adapter -- confirmed live against wedocs.unep.org's internal REST API:
this is the DSpace 7+ HAL API (`application/hal+json`, `_embedded`/`_links`
envelopes), not the legacy DSpace 6 REST API, despite the `/rest/api` path
prefix suggesting otherwise.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import config

# "ORIGINAL" is the one bundle to pull source publication files from --
# LICENSE/CC-LICENSE, THUMBNAIL, and TEXT (DSpace's own extracted-text
# cache, not to be confused with our own extraction) are structural, not
# content.
ORIGINAL_BUNDLE_NAME = "ORIGINAL"

# One auto-generated JPEG per ORIGINAL bitstream, named
# "<original filename>.jpg". Not every bitstream necessarily gets one
# (older/non-PDF content) -- callers should treat a missing match as
# normal, not an error.
THUMBNAIL_BUNDLE_NAME = "THUMBNAIL"

# A bitstream's actual DSpace-recorded MIME type is one more API call away
# (_links.format, not inlined on the bitstream itself), which is N extra
# requests per item this pass deliberately avoids. Filter and label by
# filename extension instead.
EXTENSION_MIME_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}


def mime_type_from_filename(filename: str) -> str | None:
    parts = filename.rsplit(".", 1)
    if len(parts) < 2 or not parts[1]:
        return None
    return EXTENSION_MIME_TYPES.get(parts[1].lower())


def is_supported_bitstream(filename: str) -> bool:
    return mime_type_from_filename(filename) is not None


class DspaceClient:
    def __init__(self) -> None:
        self._headers: dict[str, str] = {
            "Accept": "application/hal+json",
            "User-Agent": "UNEP-Search-Ingestion/1.0",
        }
        if config.dspace.ingest_key:
            self._headers["X-UNEP-Ingest-Key"] = config.dspace.ingest_key

        self._http = httpx.AsyncClient(timeout=config.dspace.timeout_ms / 1000)

    async def search_items(
        self, page: int, size: int, query: str | None = None, scope: str | None = None
    ) -> dict[str, Any]:
        # dsoType=item scopes Discovery to items only (not collections/
        # communities, which the same search endpoint also indexes).
        params: dict[str, str] = {"dsoType": "item", "page": str(page), "size": str(size)}
        if query:
            params["query"] = query
        if scope:
            params["scope"] = scope
        url = f"{config.dspace.api_base}/discover/search/objects?{urlencode(params)}"
        return await self._get_json(url)

    async def get_item_bundles(self, item_uuid: str) -> dict[str, Any]:
        return await self._get_json(f"{config.dspace.api_base}/core/items/{item_uuid}/bundles")

    async def get_bundle_bitstreams(self, bundle_uuid: str) -> dict[str, Any]:
        return await self._get_json(
            f"{config.dspace.api_base}/core/bundles/{bundle_uuid}/bitstreams"
        )

    def bitstream_content_url(self, bitstream_uuid: str) -> str:
        return f"{config.dspace.api_base}/core/bitstreams/{bitstream_uuid}/content"

    async def download_bitstream(self, bitstream_uuid: str) -> bytes:
        url = self.bitstream_content_url(bitstream_uuid)
        response = await self._http.get(url, headers=self._headers)
        if response.is_error:
            raise RuntimeError(
                f"DSpace {response.status_code} {response.reason_phrase} downloading "
                f"bitstream {bitstream_uuid}"
            )
        return response.content

    async def _get_json(self, url: str) -> Any:
        response = await self._http.get(url, headers=self._headers)
        if response.is_error:
            body = response.text
            raise RuntimeError(
                f"DSpace {response.status_code} {response.reason_phrase} for {url}: {body[:500]}"
            )
        return response.json()

    async def close(self) -> None:
        await self._http.aclose()
