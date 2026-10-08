"""Port of ingestor/src/dspace/mapper.ts.

Field names confirmed live against wedocs.unep.org's internal REST API:
dc.description is the summary/abstract field (not dc.description.abstract,
which doesn't exist on either item checked); dc.type is the flat
content-type display string; dc.relation.ispartof is the series signal
(dspace/series.py). dc.subject.sdg/dc.subject.theme exist and are
populated but have no provisioned field on knowledge-raw-v2 yet -- not
mapped here pending a schema decision, not an oversight.
"""

from __future__ import annotations

import re

from app.config import config
from app.drupal.mappers.common import now_iso
from app.dspace.client import mime_type_from_filename
from app.dspace.language import detect_bitstream_language
from app.dspace.series import resolve_edition_year, resolve_series
from app.shared.text import canonicalize
from app.types import DspaceBitstream, DspaceItem, DspaceRawDocument


def _first_value(item: DspaceItem, field: str) -> str | None:
    values = item.get("metadata", {}).get(field)
    if not values:
        return None
    raw = values[0].get("value")
    raw = raw.strip() if raw else None
    return raw or None


def _all_values(item: DspaceItem, field: str) -> list[str]:
    values = item.get("metadata", {}).get(field) or []
    result = []
    for v in values:
        raw = v.get("value")
        raw = raw.strip() if raw else None
        if raw:
            result.append(raw)
    return result


# DSpace dates are frequently partial (dc.date.issued as "2025-11" or just
# "2025"), but the shared index maps these fields as `date`, which needs at
# least a full calendar date -- pad missing month/day with "01" rather than
# dropping the value's precision entirely.
def _normalize_partial_date(value: str | None) -> str | None:
    if not value:
        return None
    if re.fullmatch(r"\d{4}", value):
        return f"{value}-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return f"{value}-01"
    return value


def map_dspace_bitstream(
    item: DspaceItem,
    bundle_name: str,
    bitstream: DspaceBitstream,
    run_id: str,
    image_url: str | None = None,
) -> DspaceRawDocument | None:
    if not item["inArchive"] or item["withdrawn"] or not item["discoverable"]:
        return None

    source_id = f"dspace:{item['uuid']}:{bitstream['uuid']}"
    title = _first_value(item, "dc.title") or item["name"]
    language = detect_bitstream_language(item, bitstream)["language"]

    series = resolve_series(item, title)
    date_issued = _first_value(item, "dc.date.issued")
    edition = resolve_edition_year(title, date_issued, series["name"] is not None)

    content_type_raw = _first_value(item, "dc.type")
    public_url = f"{config.dspace.public_base_url}/handle/{item['handle']}"
    check_sum = bitstream.get("checkSum") or {}

    return {
        "source": "dspace",
        "source_id": source_id,
        "parent_id": f"dspace:{item['uuid']}",
        "language": language,
        "title": title,
        "summary": _first_value(item, "dc.description"),
        "author": ", ".join(_all_values(item, "dc.contributor.author")) or None,
        "content_type_raw": content_type_raw,
        "content_type_canonical": canonicalize(content_type_raw),
        "content_type_term": None,  # no resolvable taxonomy term on this source
        "series_name": series["name"],
        "series_canonical": series["canonical"],
        "series_source": series["source"],
        "series_term": None,
        "edition_year": edition["year"],
        "edition_source": edition["source"],
        "published_at": _normalize_partial_date(date_issued),
        "public_url": public_url,
        "content_hash": None,  # set once fulltext extraction exists
        "ingest_run_id": run_id,
        "ingested_at": now_iso(),
        "fulltext": None,
        "fulltext_chars": None,
        "image_url": image_url,
        "byte_hash": None,  # set by the caller once bitstream bytes are downloaded
        "dspace": {
            "item_uuid": item["uuid"],
            "handle": item["handle"],
            "last_modified": item.get("lastModified") or None,
            "date_accessioned": _normalize_partial_date(
                _first_value(item, "dc.date.accessioned")
            ),
            "in_archive": item["inArchive"],
            "discoverable": item["discoverable"],
            "withdrawn": item["withdrawn"],
        },
        "bitstream": {
            "uuid": bitstream["uuid"],
            "bundle": bundle_name,
            "filename": bitstream["name"],
            "mime_type": mime_type_from_filename(bitstream["name"]),
            "size_bytes": bitstream["sizeBytes"],
            "checksum_algorithm": check_sum.get("checkSumAlgorithm"),
            "checksum": check_sum.get("value"),
        },
    }
