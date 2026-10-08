"""Port of ingestor/src/app.ts.

Named app_factory.py (not app.py) to avoid clashing with the `app` package
itself when imported as `from app.app_factory import create_app`.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from dateutil import parser as date_parser
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import config
from app.jobs import QUEUES
from app.rabbit import RabbitBus


def _is_date_like(value: str) -> bool:
    """Approximates JS's lenient `Date.parse` acceptance (used by the TS
    route as `!Number.isNaN(Date.parse(changedAfter))`) -- accepts more than
    strict ISO-8601 (e.g. "2024-01-01", "Jan 1 2024"). dateutil's parser has
    similarly broad (non-strict) acceptance, which is why it's used here
    instead of `datetime.fromisoformat` (that would reject inputs the TS
    version accepted)."""
    try:
        date_parser.parse(value)
        return True
    except (ValueError, OverflowError, TypeError):
        return False


def _normalize_bundles(value: object) -> list[str]:
    if not isinstance(value, list):
        return list(config.drupal.bundles)

    requested = [item for item in value if isinstance(item, str) and item.strip() != ""]

    allowed = set(config.drupal.bundles)
    unknown = [bundle for bundle in requested if bundle not in allowed]
    if unknown:
        raise RuntimeError(f"Unknown/unconfigured Drupal bundles: {', '.join(unknown)}")

    return requested if requested else list(config.drupal.bundles)


def create_app(bus: RabbitBus, lifespan: Callable[[FastAPI], Any] | None = None) -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "service": "unep-drupal-raw-ingestor"}

    @app.post("/sync/full")
    async def sync_full(request: Request) -> JSONResponse:
        body = await _read_json_body(request)
        try:
            bundles = _normalize_bundles(body.get("bundles"))
        except RuntimeError as error:
            # Preserves the TS route's behavior of letting an unknown-bundle
            # error surface as a 500 (Express 5 auto-catches a thrown/
            # rejected handler error) rather than a clean 400 -- not
            # revalidated into a nicer response as part of this 1:1 port.
            return JSONResponse(status_code=500, content={"error": str(error)})

        job = {
            "type": "sync",
            "runId": str(uuid.uuid4()),
            "mode": "full",
            "bundles": bundles,
        }
        await bus.publish(QUEUES["sync"], job)
        return JSONResponse(status_code=202, content=job)

    @app.post("/sync/incremental")
    async def sync_incremental(request: Request) -> JSONResponse:
        body = await _read_json_body(request)
        changed_after = body.get("changedAfter")
        if not isinstance(changed_after, str) or not _is_date_like(changed_after):
            return JSONResponse(
                status_code=400,
                content={"error": "changedAfter must be an ISO-8601 date/time string"},
            )

        try:
            bundles = _normalize_bundles(body.get("bundles"))
        except RuntimeError as error:
            return JSONResponse(status_code=500, content={"error": str(error)})

        job = {
            "type": "sync",
            "runId": str(uuid.uuid4()),
            "mode": "incremental",
            "bundles": bundles,
            "changedAfter": changed_after,
        }
        await bus.publish(QUEUES["sync"], job)
        return JSONResponse(status_code=202, content=job)

    return app


async def _read_json_body(request: Request) -> dict:
    """Mirrors Express's `express.json()` middleware defaulting `req.body`
    to `{}` when there's no body / it isn't JSON, rather than erroring."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- any body-parse failure -> treat as empty
        return {}
    return body if isinstance(body, dict) else {}
