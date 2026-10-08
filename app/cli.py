"""Port of ingestor/src/cli.ts.

Ad-hoc ingestion CLI for local testing/debugging: fetch, map, and index a
bundle directly in-process (no RabbitMQ), optionally capped at a doc count.
This is NOT the production path -- that's the HTTP API (/sync/full,
/sync/incremental) backed by RabbitMQ workers, which is restartable/
retryable at scale. This is for "does bundle X map/index correctly against
a handful of real records" without spinning up a queue.

Usage:
  python -m app.cli --bundle news
  python -m app.cli --bundle event --limit 10
  python -m app.cli --bundle page --limit 5 --dry-run
  python -m app.cli --bundle news --changed-after 2026-08-01T00:00:00Z
  python -m app.cli --bundle news --reconcile   (full run only, see guard below)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import sys
import uuid

from dateutil import parser as date_parser

from app.config import config
from app.drupal.client import DrupalClient, next_link
from app.drupal.mapper import DrupalMapper
from app.drupal.taxonomy_cache import TaxonomyCache
from app.opensearch.client import create_opensearch_client
from app.opensearch.index import RawIndex

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("cli")


def _is_date_like(value: str) -> bool:
    try:
        date_parser.parse(value)
        return True
    except (ValueError, OverflowError, TypeError):
        return False


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--bundle", "-b")
    parser.add_argument("--limit", "-l")
    parser.add_argument("--changed-after")
    parser.add_argument("--reconcile", action="store_true", default=False)
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser.parse_args(argv)


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not args.bundle:
        logger.error(
            "Usage: python -m app.cli --bundle <name> [--limit <n>] "
            "[--changed-after <iso8601>] [--reconcile] [--dry-run]\n"
            "Configured bundles (DRUPAL_BUNDLES): %s",
            ", ".join(config.drupal.bundles),
        )
        return 1

    bundle = args.bundle
    if bundle not in config.drupal.bundles:
        logger.error(
            'Bundle "%s" is not in DRUPAL_BUNDLES (%s). Add it there first -- that list is the '
            "enable/disable gate for what's safe to ingest.",
            bundle,
            ", ".join(config.drupal.bundles),
        )
        return 1

    limit: float | None = None
    if args.limit:
        try:
            limit = float(args.limit)
        except ValueError:
            limit = None
        if limit is None or not math.isfinite(limit) or limit <= 0:
            logger.error("--limit must be a positive number, got: %s", args.limit)
            return 1

    changed_after = args.changed_after
    if changed_after is not None and not _is_date_like(changed_after):
        logger.error("--changed-after must be an ISO-8601 date/time string, got: %s", changed_after)
        return 1
    mode = "incremental" if changed_after else "full"

    reconcile = args.reconcile is True
    if reconcile and limit is not None:
        logger.error(
            "--reconcile requires an unbounded run (no --limit): a capped run never sees the "
            "whole bundle, so treating unseen docs as deleted/unpublished would wrongly remove "
            "live content."
        )
        return 1
    if reconcile and mode == "incremental":
        logger.error(
            "--reconcile only applies to a full run (no --changed-after): an incremental run "
            "only looks at recently changed content, so most of the bundle would look 'unseen' "
            "and get deleted."
        )
        return 1
    if reconcile and args.dry_run:
        logger.error(
            "--reconcile has no effect with --dry-run (nothing gets written to reconcile against)."
        )
        return 1

    dry_run = args.dry_run is True
    run_id = str(uuid.uuid4())

    logger.info(
        "[cli] bundle=%s mode=%s limit=%s reconcile=%s dry_run=%s run_id=%s",
        bundle,
        mode,
        limit if limit is not None else "none",
        reconcile,
        dry_run,
        run_id,
    )

    drupal = DrupalClient()
    taxonomies = TaxonomyCache(drupal)
    mapper = DrupalMapper(taxonomies)

    opensearch_client = None if dry_run else create_opensearch_client()
    raw_index = RawIndex(opensearch_client) if opensearch_client else None
    if raw_index:
        await asyncio.to_thread(raw_index.ensure_index)

    # Needed for TaxonomyCache.find_term_by_name (series-from-tags inference
    # in report_flagship.py) to have anything to search -- without this the
    # cache only ever holds terms resolved lazily by uuid, never a full
    # vocabulary to scan by name. page_worker.py does this too.
    await taxonomies.preload_configured()

    url: str | None = drupal.collection_url(bundle, changed_after=changed_after)
    indexed = 0
    skipped = 0

    while url and (limit is None or indexed < limit):
        response = await drupal.get_collection(url)

        remaining = len(response["data"]) if limit is None else int(limit) - indexed
        resources = response["data"][:remaining]

        mapped = await asyncio.gather(
            *(mapper.map(resource, bundle, run_id) for resource in resources)
        )
        documents = [doc for doc in mapped if doc is not None]
        skipped += len(mapped) - len(documents)

        if documents:
            if dry_run:
                for doc in documents:
                    logger.info(
                        '[dry-run] would index %s %s "%s"',
                        doc["bundle"],
                        doc["source_id"],
                        doc["title"],
                    )
            else:
                await asyncio.to_thread(raw_index.bulk_index, documents)

        indexed += len(documents)
        limit_suffix = f"/{int(limit)}" if limit is not None else ""
        logger.info(
            "[cli] page done: fetched=%d processed=%d indexed_so_far=%d%s",
            len(response["data"]),
            len(resources),
            indexed,
            limit_suffix,
        )

        url = next_link(response.get("links")) if (limit is None or indexed < limit) else None

    logger.info(
        "[cli] complete. bundle=%s indexed=%d skipped_unpublished=%d dry_run=%s",
        bundle,
        indexed,
        skipped,
        dry_run,
    )

    if reconcile:
        result = await asyncio.to_thread(raw_index.reconcile_bundle, bundle, run_id)
        logger.info(
            "[reconcile] bundle=%s languages=%s deleted=%d",
            bundle,
            ",".join(result["languages"]) or "none",
            result["deleted"],
        )

    if opensearch_client is not None:
        await asyncio.to_thread(opensearch_client.close)
    await drupal.close()
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(run())
    except Exception:  # noqa: BLE001 -- mirrors main().catch(...) -> exit(1)
        logger.exception("[cli] failed")
        sys.exit(1)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
