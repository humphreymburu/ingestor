"""Port of ingestor/src/workers/sync.worker.ts."""

from __future__ import annotations

import logging

from app.drupal.client import DrupalClient
from app.jobs import QUEUES
from app.rabbit import RabbitBus

logger = logging.getLogger("sync")


async def start_sync_worker(bus: RabbitBus, drupal: DrupalClient) -> None:
    async def handle(job: dict, _message) -> None:
        if job.get("type") != "sync":
            raise RuntimeError("Invalid sync job")

        for bundle in job["bundles"]:
            page_job = {
                "type": "page",
                "runId": job["runId"],
                "mode": job["mode"],
                "bundle": bundle,
                "url": drupal.collection_url(
                    bundle,
                    changed_after=job.get("changedAfter") if job["mode"] == "incremental" else None,
                ),
            }
            await bus.publish(QUEUES["page"], page_job)

        logger.info(
            "[sync] run=%s mode=%s queued bundles=%s",
            job["runId"],
            job["mode"],
            ",".join(job["bundles"]),
        )

    await bus.consume(QUEUES["sync"], handle)
