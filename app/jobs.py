"""Port of ingestor/src/jobs.ts (queue names) and the SyncJob/PageJob shapes
from ingestor/src/types.ts.

Field names use pydantic aliases to keep the on-the-wire JSON in the same
camelCase shape the TS service publishes/consumes (`runId`, `changedAfter`,
...) -- this matters during any cutover window where old TS-published
messages might still be sitting in a retry/dead queue. Always serialize with
`model_dump(by_alias=True)` (or `.model_dump_json(by_alias=True)`) when
publishing, and construct via `SyncJob(**data)` / `SyncJob.model_validate(data)`
(both accept alias or field name, since `populate_by_name=True`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QUEUES = {
    "sync": "drupal.sync",
    "page": "drupal.page",
    "dead": "drupal.dead",
}


class SyncJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["sync"] = "sync"
    run_id: str = Field(alias="runId")
    mode: Literal["full", "incremental"]
    bundles: list[str]
    changed_after: str | None = Field(default=None, alias="changedAfter")


class PageJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["page"] = "page"
    run_id: str = Field(alias="runId")
    mode: Literal["full", "incremental"]
    bundle: str
    url: str
