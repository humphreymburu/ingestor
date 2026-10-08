"""Port of ingestor/src/config.ts.

Deliberately implemented with plain functions mirroring the TS helpers
(`env`/`intEnv`/`csvEnv`/`boolEnv`/`buildRabbitUrl`) rather than fighting
pydantic's coercion/validation, so the same quirky-but-load-bearing behavior
carries over exactly:

- `env(name)` with no fallback raises if the var is unset (eager, at import
  time -- a missing required var crashes the process on startup, same as TS).
- `int_env`/`bool_env`/`csv_env` intentionally do NOT validate their inputs
  beyond "is this parseable as a number" -- e.g. `PORT=""` becomes `0`
  (mirrors JS's `Number("") === 0`), and `OPENSEARCH_AUTH`/`OPENSEARCH_SERVICE`
  are plain unvalidated strings (an unrecognized value silently falls through
  to the "no special auth" branch in opensearch/client.py, exactly as it does
  in the TS `createOpenSearchClient`).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from urllib.parse import quote


def env(name: str, fallback: str | None = None) -> str:
    value = os.environ.get(name, fallback)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def optional_env(name: str) -> str | None:
    """Mirrors `process.env[name] || undefined` -- empty string counts as unset."""
    return os.environ.get(name) or None


def _env_or(name: str, fallback: str) -> str:
    """Mirrors `process.env[name] ?? fallback` -- only a *missing* var falls
    back; an explicitly empty string is returned as-is."""
    value = os.environ.get(name)
    return value if value is not None else fallback


def _js_number(raw: str) -> float:
    """Approximates JS `Number(str)` coercion for the realistic inputs these
    env vars carry (plain integers/decimals, or an empty/whitespace string,
    which JS coerces to 0). Exotic JS numeric literals (hex, "Infinity") are
    not specially handled -- not expected in PORT/timeout/etc. env vars."""
    stripped = raw.strip()
    if stripped == "":
        return 0.0
    return float(stripped)


def int_env(name: str, fallback: int) -> int | float:
    raw = os.environ.get(name)
    text = raw if raw is not None else str(fallback)
    try:
        value = _js_number(text)
    except ValueError:
        raise RuntimeError(f"{name} must be a number") from None
    if not math.isfinite(value):
        raise RuntimeError(f"{name} must be a number")
    return int(value) if value == int(value) else value


def csv_env(name: str, fallback: str = "") -> list[str]:
    raw = os.environ.get(name, fallback)
    return [item.strip() for item in raw.split(",") if item.strip()]


def bool_env(name: str, fallback: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return fallback
    return value in ("true", "1")


def strip_trailing_slash(value: str) -> str:
    """Mirrors `value.replace(/\\/$/, "")` -- removes at most one trailing slash."""
    return value[:-1] if value.endswith("/") else value


def build_rabbit_url() -> str:
    """Prefer discrete RABBITMQ_HOST/USER/PASSWORD/... vars so the password can
    be injected as its own secret (e.g. an ECS task definition `secrets` entry
    from Secrets Manager) without baking it into a composite URL. RABBITMQ_URL
    remains a plain override for local dev (docker-compose RabbitMQ, etc.)."""
    explicit_url = os.environ.get("RABBITMQ_URL")
    host = os.environ.get("RABBITMQ_HOST")

    if not host:
        return explicit_url or "amqp://guest:guest@localhost:5672"
    if explicit_url:
        raise RuntimeError(
            "Set either RABBITMQ_URL or RABBITMQ_HOST (+ related vars), not both"
        )

    user = quote(env("RABBITMQ_USER"), safe="")
    password = quote(env("RABBITMQ_PASSWORD"), safe="")
    tls = bool_env("RABBITMQ_TLS", True)
    port = int_env("RABBITMQ_PORT", 5671 if tls else 5672)
    vhost = quote(_env_or("RABBITMQ_VHOST", "/"), safe="")
    heartbeat = int_env("RABBITMQ_HEARTBEAT", 30)
    scheme = "amqps" if tls else "amqp"

    return f"{scheme}://{user}:{password}@{host}:{port}/{vhost}?heartbeat={heartbeat}"


@dataclass(frozen=True)
class DrupalConfig:
    api_base: str
    page_limit: int | float
    bundles: list[str]
    timeout_ms: int | float
    bearer_token: str | None
    basic_user: str | None
    basic_password: str | None
    taxonomy_resource_types: list[str]


@dataclass(frozen=True)
class RabbitConfig:
    url: str
    exchange: str
    retry_delay_ms: int | float
    max_retries: int | float
    prefetch: int | float


@dataclass(frozen=True)
class OpenSearchConfig:
    node: str
    index: str
    auth: str  # "aws" | "basic" | "none", unvalidated -- see module docstring
    region: str
    service: str  # "es" | "aoss", unvalidated
    username: str | None
    password: str | None


@dataclass(frozen=True)
class DspaceConfig:
    api_base: str
    public_base_url: str
    ingest_key: str | None
    timeout_ms: int | float


@dataclass(frozen=True)
class MistralOcrConfig:
    api_key: str | None
    api_base: str
    timeout_ms: int | float


@dataclass(frozen=True)
class Config:
    port: int | float
    drupal: DrupalConfig
    rabbit: RabbitConfig
    opensearch: OpenSearchConfig
    dspace: DspaceConfig
    public_base_url: str
    mistral_ocr: MistralOcrConfig


def load_config() -> Config:
    return Config(
        port=int_env("PORT", 3000),
        drupal=DrupalConfig(
            api_base=strip_trailing_slash(env("DRUPAL_API_BASE")),
            page_limit=int_env("DRUPAL_PAGE_LIMIT", 50),
            bundles=csv_env("DRUPAL_BUNDLES", "news"),
            timeout_ms=int_env("DRUPAL_HTTP_TIMEOUT_MS", 30_000),
            bearer_token=optional_env("DRUPAL_BEARER_TOKEN"),
            basic_user=optional_env("DRUPAL_BASIC_USER"),
            basic_password=optional_env("DRUPAL_BASIC_PASSWORD"),
            taxonomy_resource_types=csv_env(
                "DRUPAL_TAXONOMY_RESOURCE_TYPES",
                "taxonomy_term--content_types,taxonomy_term--un_environment_topics,"
                "taxonomy_term--un_environment_regions,taxonomy_term--tags",
            ),
        ),
        rabbit=RabbitConfig(
            url=build_rabbit_url(),
            exchange=env("RABBITMQ_EXCHANGE", "unep.drupal.ingest"),
            retry_delay_ms=int_env("RABBITMQ_RETRY_DELAY_MS", 5_000),
            max_retries=int_env("RABBITMQ_MAX_RETRIES", 5),
            prefetch=int_env("RABBITMQ_PREFETCH", 4),
        ),
        opensearch=OpenSearchConfig(
            node=env("OPENSEARCH_NODE"),
            index=env("OPENSEARCH_INDEX", "knowledge-raw-v2"),
            auth=env("OPENSEARCH_AUTH", "aws"),
            region=env("AWS_REGION", "eu-west-1"),
            service=env("OPENSEARCH_SERVICE", "es"),
            username=optional_env("OPENSEARCH_USERNAME"),
            password=optional_env("OPENSEARCH_PASSWORD"),
        ),
        # See ingestor/src/config.ts for the full WEDOCS/Cloudflare access
        # history behind these defaults -- apiBase points at whatever
        # internal/tunnel path is currently reachable, publicBaseUrl stays
        # the human-facing host for citable handle links.
        dspace=DspaceConfig(
            api_base=strip_trailing_slash(
                env("WEDOCS_API_BASE", "https://wedocs.unep.org/rest/api")
            ),
            public_base_url=strip_trailing_slash(
                env("WEDOCS_PUBLIC_BASE_URL", "https://wedocs.unep.org")
            ),
            ingest_key=optional_env("UNEP_INGEST_KEY"),
            timeout_ms=int_env("WEDOCS_HTTP_TIMEOUT_MS", 30_000),
        ),
        public_base_url=strip_trailing_slash(env("PUBLIC_BASE_URL", "https://www.unep.org")),
        # Managed-fallback OCR -- apiKey unset (the common case until this is
        # actually provisioned) means the route is simply unavailable, not a
        # startup failure; Tesseract (pdf/ocr.py) remains the primary/required
        # OCR path.
        mistral_ocr=MistralOcrConfig(
            api_key=optional_env("MISTRAL_API_KEY"),
            api_base=env("MISTRAL_API_BASE", "https://api.mistral.ai"),
            timeout_ms=int_env("MISTRAL_OCR_TIMEOUT_MS", 60_000),
        ),
    )


config = load_config()
