"""Port of ingestor/src/index.ts -- production entrypoint.

Runs the FastAPI app (app/app_factory.py, port of app.ts) and both RabbitMQ
consumers (workers/) in one process/event loop, matching the TS service's
single-process shape exactly (not split into separate web/worker
containers). Startup order mirrors index.ts: ensure the OpenSearch index,
connect RabbitMQ, register both consumers, THEN start serving HTTP -- if
OpenSearch or RabbitMQ fail to initialize, the process must not come up
"partially healthy".
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from app.app_factory import create_app
from app.config import config
from app.drupal.client import DrupalClient
from app.drupal.mapper import DrupalMapper
from app.drupal.taxonomy_cache import TaxonomyCache
from app.opensearch.client import create_opensearch_client
from app.opensearch.index import RawIndex
from app.rabbit import RabbitBus
from app.workers.page_worker import start_page_worker
from app.workers.sync_worker import start_sync_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("main")

# Constructed at import time (not connected yet) so the route closures in
# create_app() can reference it; actually connecting happens in lifespan
# startup below, tied to uvicorn's event loop.
bus = RabbitBus()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    drupal = DrupalClient()
    taxonomies = TaxonomyCache(drupal)
    mapper = DrupalMapper(taxonomies)

    opensearch_client = create_opensearch_client()
    raw_index = RawIndex(opensearch_client)
    # opensearch-py is a sync client -- run its blocking HTTP call off the
    # event loop rather than stalling every other coroutine during startup.
    await asyncio.to_thread(raw_index.ensure_index)

    await bus.connect()

    # Both `start_*_worker` calls resolve once their consumer is registered
    # with the broker, not when message processing ends -- consumption
    # continues in the background for the life of the channel.
    await asyncio.gather(
        start_sync_worker(bus, drupal),
        start_page_worker(bus, drupal, taxonomies, mapper, raw_index),
    )

    logger.info("unep-drupal-raw-ingestor listening on :%s", config.port)
    try:
        yield
    finally:
        logger.info("shutting down...")
        await bus.close()
        await drupal.close()
        await asyncio.to_thread(opensearch_client.close)


app = create_app(bus, lifespan=lifespan)


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=int(config.port))


if __name__ == "__main__":
    main()
