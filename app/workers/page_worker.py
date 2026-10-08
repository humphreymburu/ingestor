"""Port of ingestor/src/workers/page.worker.ts."""

from __future__ import annotations

import asyncio
import logging

from app.drupal.client import DrupalClient, next_link
from app.drupal.mapper import DrupalMapper
from app.drupal.taxonomy_cache import TaxonomyCache
from app.jobs import QUEUES
from app.opensearch.index import RawIndex
from app.rabbit import RabbitBus

logger = logging.getLogger("page")


async def start_page_worker(
    bus: RabbitBus,
    drupal: DrupalClient,
    taxonomies: TaxonomyCache,
    mapper: DrupalMapper,
    raw_index: RawIndex,
) -> None:
    async def handle(job: dict, _message) -> None:
        if job.get("type") != "page":
            raise RuntimeError("Invalid page job")

        # Taxonomies are small reference datasets; preload configured
        # vocabularies once, then lazily resolve any relationship types not
        # preconfigured.
        await taxonomies.preload_configured()

        response = await drupal.get_collection(job["url"])

        mapped = await asyncio.gather(
            *(mapper.map(resource, job["bundle"], job["runId"]) for resource in response["data"])
        )
        documents = [doc for doc in mapped if doc is not None]

        # bulk_index does synchronous HTTP under the hood (opensearch-py) --
        # run it off the event loop so it doesn't block other consumers'
        # messages sharing this process.
        await asyncio.to_thread(raw_index.bulk_index, documents)

        logger.info(
            "[page] run=%s bundle=%s fetched=%d indexed=%d",
            job["runId"],
            job["bundle"],
            len(response["data"]),
            len(documents),
        )

        next_url = next_link(response.get("links"))
        if next_url:
            next_job = {**job, "url": next_url}
            await bus.publish(QUEUES["page"], next_job)
            return

        logger.info("[page] run=%s bundle=%s complete", job["runId"], job["bundle"])

        # Only a genuinely unbounded full-mode run walks every page for the
        # bundle, so only that run's completion is a valid basis for
        # deciding "not reaffirmed by this run" == "deleted/unpublished in
        # Drupal". See RawIndex.reconcile_bundle for the full rationale/
        # safety notes.
        if job["mode"] == "full":
            result = await asyncio.to_thread(
                raw_index.reconcile_bundle, job["bundle"], job["runId"]
            )
            logger.info(
                "[reconcile] run=%s bundle=%s languages=%s deleted=%d",
                job["runId"],
                job["bundle"],
                ",".join(result["languages"]) or "none",
                result["deleted"],
            )

    await bus.consume(QUEUES["page"], handle)
