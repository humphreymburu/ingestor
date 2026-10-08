"""Port of ingestor/src/dspace-cli.ts.

Ad-hoc DSpace ingestion CLI, mirroring cli.py's Drupal shape: fetch, map,
and index real items/bitstreams directly in-process (no RabbitMQ), capped
at an item count.

This is a MANUAL, keyboard-attended tool. It talks to the DSpace API
through whatever WEDOCS_API_BASE points at -- today, that's almost
certainly an SSM port-forward tunnel that has repeatedly proven unable to
survive sustained/unattended traffic. Run it, watch it, stop it. Do NOT
wire this logic into a RabbitMQ consumer until there's a real, stable,
unattended-capable access path -- a queue consumer runs indefinitely
unattended, which is exactly the failure mode this tunnel can't survive.

Usage:
  python -m app.dspace_cli --limit 5 --dry-run
  python -m app.dspace_cli --scope e03d662e-a075-4072-b226-470770fd6198 --limit 14
  python -m app.dspace_cli --query "emissions gap" --dry-run
  python -m app.dspace_cli --scope <uuid> --limit 3 --extract-fulltext --dry-run

--extract-fulltext downloads each bitstream's real bytes and runs the PDF
extraction pipeline (app/pdf/) to populate fulltext/fulltext_chars/
content_hash -- off by default because it's slow (network + per-page
routing, possibly OCR) and most manual test runs just want to confirm
metadata mapping. map_dspace_bitstream() itself never fetches bytes, so
this step is done here as a deliberate post-processing pass, keeping the
mapper pure/network-free and unit-testable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import uuid

from app.dspace.client import (
    ORIGINAL_BUNDLE_NAME,
    THUMBNAIL_BUNDLE_NAME,
    DspaceClient,
    is_supported_bitstream,
)
from app.dspace.mapper import map_dspace_bitstream
from app.opensearch.client import create_opensearch_client
from app.opensearch.index import RawIndex
from app.shared.text import byte_hash, content_hash

PAGE_SIZE = 20

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("dspace-cli")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--limit", "-l")
    parser.add_argument("--scope")
    parser.add_argument("--query", "-q")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--extract-fulltext", action="store_true", default=False)
    return parser.parse_args(argv)


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    limit: float | None = None
    if args.limit:
        try:
            limit = float(args.limit)
        except ValueError:
            limit = None
        if limit is None or not math.isfinite(limit) or limit <= 0:
            logger.error("--limit must be a positive number, got: %s", args.limit)
            return 1

    dry_run = args.dry_run is True
    extract_fulltext_enabled = args.extract_fulltext is True
    run_id = str(uuid.uuid4())

    logger.info(
        "[dspace-cli] scope=%s query=%s limit=%s dry_run=%s extract_fulltext=%s run_id=%s",
        args.scope or "none (whole repository)",
        args.query or "none",
        limit if limit is not None else "none",
        dry_run,
        extract_fulltext_enabled,
        run_id,
    )

    dspace = DspaceClient()
    opensearch_client = None if dry_run else create_opensearch_client()
    raw_index = RawIndex(opensearch_client) if opensearch_client else None
    if raw_index:
        await asyncio.to_thread(raw_index.ensure_index)

    page = 0
    items_seen = 0
    items_processed = 0
    items_skipped_no_original = 0
    bitstreams_skipped_unsupported = 0
    documents_indexed = 0
    documents_skipped_not_discoverable = 0

    while True:
        response = await dspace.search_items(
            page=page, size=PAGE_SIZE, query=args.query, scope=args.scope
        )

        search_result = response["_embedded"]["searchResult"]
        page_info = search_result["page"]
        items = [
            obj["_embedded"]["indexableObject"]
            for obj in search_result["_embedded"]["objects"]
        ]

        stop_paging = False
        for item in items:
            items_seen += 1
            if limit is not None and items_processed >= limit:
                stop_paging = True
                break

            bundles_response = await dspace.get_item_bundles(item["uuid"])
            bundles = bundles_response["_embedded"]["bundles"]
            original_bundle = next(
                (b for b in bundles if b["name"] == ORIGINAL_BUNDLE_NAME), None
            )
            if original_bundle is None:
                items_skipped_no_original += 1
                logger.info(
                    '[dspace-cli] skip (no ORIGINAL bundle): %s "%s"', item["uuid"], item["name"]
                )
                items_processed += 1
                continue

            bitstreams_response = await dspace.get_bundle_bitstreams(original_bundle["uuid"])

            # THUMBNAIL is a sibling bundle, not nested under ORIGINAL -- one
            # extra list call per item, reusing the bundle list already
            # fetched above. Build filename -> content-url once per item
            # rather than per bitstream. A missing THUMBNAIL bundle (or no
            # matching filename) just means no thumbnail for this item --
            # not an error.
            thumbnail_bundle = next(
                (b for b in bundles if b["name"] == THUMBNAIL_BUNDLE_NAME), None
            )
            thumbnail_url_by_filename: dict[str, str] = {}
            if thumbnail_bundle is not None:
                thumbnail_bitstreams = await dspace.get_bundle_bitstreams(
                    thumbnail_bundle["uuid"]
                )
                for thumb in thumbnail_bitstreams["_embedded"]["bitstreams"]:
                    thumbnail_url_by_filename[thumb["name"]] = dspace.bitstream_content_url(
                        thumb["uuid"]
                    )

            documents = []

            for bitstream in bitstreams_response["_embedded"]["bitstreams"]:
                if not is_supported_bitstream(bitstream["name"]):
                    bitstreams_skipped_unsupported += 1
                    logger.info(
                        "[dspace-cli] skip (unsupported extension): %s", bitstream["name"]
                    )
                    continue

                image_url = thumbnail_url_by_filename.get(f"{bitstream['name']}.jpg")
                doc = map_dspace_bitstream(
                    item, original_bundle["name"], bitstream, run_id, image_url
                )
                if doc is None:
                    documents_skipped_not_discoverable += 1
                    logger.info(
                        '[dspace-cli] skip (withdrawn/not discoverable/not in archive): %s "%s"',
                        item["uuid"],
                        item["name"],
                    )
                    continue

                if extract_fulltext_enabled:
                    await _maybe_extract_fulltext(doc, dspace, bitstream, raw_index)

                documents.append(doc)

            if documents:
                if dry_run:
                    for doc in documents:
                        logger.info(
                            '[dry-run] would index dspace:%s %s "%s" lang=%s bitstream="%s" '
                            "fulltext_chars=%s image_url=%s",
                            doc["bitstream"]["bundle"],
                            doc["source_id"],
                            doc["title"],
                            doc["language"],
                            doc["bitstream"]["filename"],
                            doc["fulltext_chars"] if doc["fulltext_chars"] is not None else "null",
                            doc["image_url"] if doc["image_url"] is not None else "null",
                        )
                else:
                    await asyncio.to_thread(raw_index.bulk_index, documents)
                documents_indexed += len(documents)

            items_processed += 1

        limit_suffix = f"/{int(limit)}" if limit is not None else ""
        logger.info(
            "[dspace-cli] page %d/%d done: items_processed_so_far=%d%s",
            page_info["number"] + 1,
            page_info["totalPages"],
            items_processed,
            limit_suffix,
        )

        if stop_paging:
            break
        if page_info["number"] + 1 >= page_info["totalPages"]:
            break
        if limit is not None and items_processed >= limit:
            break
        page += 1

    logger.info(
        "[dspace-cli] complete. items_seen=%d items_processed=%d documents_indexed=%d "
        "dry_run=%s | skipped: no_original_bundle=%d unsupported_bitstream=%d not_discoverable=%d",
        items_seen,
        items_processed,
        documents_indexed,
        dry_run,
        items_skipped_no_original,
        bitstreams_skipped_unsupported,
        documents_skipped_not_discoverable,
    )

    if opensearch_client is not None:
        await asyncio.to_thread(opensearch_client.close)
    await dspace.close()
    return 0


async def _maybe_extract_fulltext(doc, dspace: DspaceClient, bitstream, raw_index: RawIndex | None) -> None:
    if doc["bitstream"]["mime_type"] != "application/pdf":
        logger.info(
            "[dspace-cli] fulltext extraction skipped (only PDF is built so far, got %s): %s",
            doc["bitstream"]["mime_type"],
            doc["bitstream"]["filename"],
        )
        return

    # Deferred import: app.pdf.extract depends on the PDF extraction
    # subsystem, which is a separate, larger porting phase -- importing it
    # lazily here means this CLI (and its non-extraction tests) work fine
    # even before that subsystem lands.
    from app.pdf.extract import extract_fulltext

    try:
        content = await dspace.download_bitstream(bitstream["uuid"])
        doc["byte_hash"] = byte_hash(content)

        # Stage-1 exact-duplicate detection -- only meaningful against the
        # live index, so skipped entirely in --dry-run (nothing to query).
        # Surfaces the finding; takes no merge/delete action.
        if raw_index is not None:
            duplicates = await asyncio.to_thread(
                raw_index.find_by_byte_hash, doc["byte_hash"], doc["source_id"]
            )
            if duplicates:
                logger.warning(
                    "[dspace-cli]   byte_hash duplicate: %s matches %d already-indexed "
                    "document(s): %s",
                    doc["bitstream"]["filename"],
                    len(duplicates),
                    ", ".join(duplicates),
                )

        result = await extract_fulltext(content)
        doc["fulltext"] = result["fulltext"]
        doc["fulltext_chars"] = result["fulltextChars"]
        doc["content_hash"] = content_hash(result["fulltext"])
        warn_pages = [c for c in result["completeness"] if c["status"] == "WARN"]
        ocr_counts = result["ocrBackendCounts"]
        ocr_backends_used = ocr_counts["TESSERACT"] + ocr_counts["MISTRAL_OCR"] > 0

        message = (
            f"[dspace-cli] extracted {doc['bitstream']['filename']}: pages={result['pageCount']} "
            f"chars={result['fulltextChars']} routes={json.dumps(result['routeCounts'])} "
            f"numeric_tokens={result['numericTokens']['total']}"
        )
        if ocr_backends_used:
            message += f" ocr_backends={json.dumps(ocr_counts)}"
        if result["failedPages"]:
            message += f" FAILED_PAGES={len(result['failedPages'])}"
        if warn_pages:
            message += f" COMPLETENESS_WARN_PAGES={len(warn_pages)}"
        logger.info(message)

        # A partial failure still indexes -- the fulltext just has one or
        # more pages missing. Surface it loudly rather than let it look
        # indistinguishable from a clean extraction.
        for failed in result["failedPages"]:
            logger.error(
                "[dspace-cli]   page %d (%s) extraction failed: %s",
                failed["pageNumber"],
                failed["route"],
                failed["error"],
            )
        # A large excluded-character share is worth a human glance (real
        # dense front-matter, or a false-positive header/footer match
        # eating real body text) -- not an error, but not silent either.
        for warn in warn_pages:
            logger.warning(
                "[dspace-cli]   page %d completeness WARN: excluded %d/%d chars (%s)",
                warn["pageNumber"],
                warn["excludedCharacterCount"],
                warn["sourceCharacterCount"],
                json.dumps(warn["excludedReasons"]),
            )
    except Exception:  # noqa: BLE001 -- mirrors the TS catch-and-continue
        logger.exception(
            "[dspace-cli] fulltext extraction FAILED for %s", doc["bitstream"]["filename"]
        )


def main() -> None:
    try:
        exit_code = asyncio.run(run())
    except Exception:  # noqa: BLE001 -- mirrors main().catch(...) -> exit(1)
        logger.exception("[dspace-cli] failed")
        sys.exit(1)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
