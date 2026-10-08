# ingestor-py

Python (FastAPI + aio-pika) port of `../ingestor` (the TypeScript
`unep-drupal-raw-ingestor` service). See `../.claude/plans/wise-bouncing-glacier.md`
in this repo's Claude Code plan history for the full porting plan and its
rationale; this README covers what's here today.

The TypeScript service under `../ingestor` remains the source of truth and
stays in place until this port is verified working end-to-end against real
Drupal/OpenSearch/RabbitMQ endpoints -- do not delete it as part of this
work.

## Status

Ported and tested (217 tests + 5 environment-dependent skips, `pytest`):

- `app/config.py` -- env var loading, mirrors `ingestor/src/config.ts` quirks exactly.
- `app/shared/text.py` -- `canonicalize`/`content_hash`/`byte_hash`/`title_year`/`infer_series_name_from_title`.
- `app/opensearch/` -- OpenSearch client (aws/basic/none auth) + `RawIndex` (the full `dynamic:"strict"` mapping, bulk indexing, reconcile-by-run-id, byte-hash dedup lookup).
- `app/jobs.py`, `app/rabbit.py` -- queue topology, retry/dead-letter via `aio-pika`.
- `app/drupal/` -- JSON:API client, taxonomy cache (preload + lazy resolve), and all four bundle mappers (`news`, `event`, `page`, `report_flagship`).
- `app/workers/`, `app/app_factory.py`, `app/main.py`, `app/cli.py` -- the sync/page RabbitMQ consumers, the FastAPI `/health` + `/sync/full` + `/sync/incremental` routes, the production entrypoint, and the ad-hoc Drupal ingest CLI.
- `app/dspace/`, `app/dspace_cli.py` -- DSpace 7+ HAL API client, 4-tier language detection, series/edition resolution, the bitstream mapper, and the ad-hoc DSpace ingest CLI (manual/attended only, matching the TS side -- no DSpace queue workers exist there either).
- `app/pdf/` -- the full PDF extraction subsystem: native text/geometry extraction (**pdfplumber**, replacing pdfjs-dist -- see `native.py`'s module docstring for the licensing rationale and the fidelity risk this swap carries), routing, heading detection, header/footer/page-number reconstruction, completeness accounting, numeric-token counting, cross-page continuation, Tesseract + Mistral OCR, and the production flat-fulltext orchestrator (`extract.py`); plus the non-production canonical DoclingDocument graph pipeline (`segmentation.py`, `docling_types.py`, `docling_mapper.py`, `completeness_graph.py`, `canonical_pipeline.py`, `parser_capability.py`).

See [docs/PDF_EXTRACTION_EXPLAINED.md](docs/PDF_EXTRACTION_EXPLAINED.md)
for a plain-language, paragraph-per-module walkthrough of what the PDF
extraction subsystem actually does and why.

See [docs/PDF_EXTRACTION_FLOW.md](docs/PDF_EXTRACTION_FLOW.md) for a
terminal-diagram view of the PDF extraction data flow (both the
production flat-fulltext path and the non-production canonical
DoclingDocument graph path).

See [docs/PORTING_DECISIONS.md](docs/PORTING_DECISIONS.md) for the
phase-by-phase rationale behind this port — why each phase was ordered
the way it was, which library was picked over its alternatives and why,
and where TS's behavior was kept as-is (bugs and quirks included) rather
than "improved."

See [docs/PDF_ARCHITECTURE_REMAINING_TASKS.md](docs/PDF_ARCHITECTURE_REMAINING_TASKS.md)
for exactly how far the PDF subsystem follows
`../ingestor/typescript_pdf_extraction_rag_architecture_v2.2.md` (short
answer: it matches the TypeScript build's scope exactly, which is itself
a deliberately reduced slice of that document — not the full thing).

Not yet ported:

- `Dockerfile` / Terraform deployment wiring -- the TS service has neither today either, so this is new work, not a port. Deliberately deferred (see the porting plan) until the app itself is verified against real endpoints.
- The runtime container will need `poppler-utils` (`pdftoppm`) and `tesseract-ocr` installed for the OCR route to work -- same requirement the TS service has, tracked as a Dockerfile follow-up.

## Local development

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # fill in DRUPAL_API_BASE / OPENSEARCH_NODE at minimum
./.venv/bin/pytest
```

Running the service locally also needs a RabbitMQ broker -- reuse
`../ingestor/docker-compose.yml` (or copy it here) to start one:
`rabbitmq:4-management` on 5672/15672, guest/guest.

```bash
./.venv/bin/python -m app.main            # production-shaped entrypoint (FastAPI + both workers)
./.venv/bin/python -m app.cli --bundle news --limit 5 --dry-run   # ad-hoc CLI, no RabbitMQ needed
```

## Verification against the TypeScript service

Before treating any bundle as safe to cut over, cross-check a sample of
Python-produced documents field-by-field against what `../ingestor`
currently produces for the same Drupal nodes (same `source_id`, same
`content_hash`, same `series_canonical`/`content_type_canonical` values).
Run any indexing smoke test against a disposable/scratch OpenSearch index
name, never the shared staging `knowledge-raw-v2` index, without explicit
sign-off.
