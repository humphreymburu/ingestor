"""Port of ingestor/src/rabbit.ts.

Uses aio-pika. Deliberately uses `aio_pika.connect` (not `connect_robust`) --
there is no reconnect logic here, matching the TS service exactly: CloudAMQP
(and any remote broker) can drop the TCP connection without warning, and
this hard-exits the process rather than silently running with a dead bus,
letting the process supervisor (Fargate task/ECS, systemd, etc.) restart
cleanly. `close()` sets a `_closing` flag first so a deliberate shutdown
doesn't trigger that exit path.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika import ExchangeType
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractExchange,
    AbstractIncomingMessage,
)

from app.config import config
from app.jobs import QUEUES

logger = logging.getLogger("rabbit")

JobHandler = Callable[[dict[str, Any], AbstractIncomingMessage], Awaitable[None]]


class RabbitBus:
    def __init__(self) -> None:
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None
        self._closing = False
        # Overridable in tests -- production always hard-exits on an
        # unexpected connection close (see _on_connection_close).
        self._process_exit: Callable[[int], None] = os._exit

    async def connect(self) -> None:
        self._connection = await aio_pika.connect(config.rabbit.url)
        self._connection.close_callbacks.add(self._on_connection_close)

        self._channel = await self._connection.channel()
        self._channel.close_callbacks.add(self._on_channel_close)
        await self._channel.set_qos(prefetch_count=int(config.rabbit.prefetch))

        self._exchange = await self._channel.declare_exchange(
            config.rabbit.exchange, ExchangeType.DIRECT, durable=True
        )

        for queue_name in (QUEUES["sync"], QUEUES["page"]):
            await self._assert_main_and_retry_queue(queue_name)

        dead_queue = await self._channel.declare_queue(QUEUES["dead"], durable=True)
        await dead_queue.bind(self._exchange, routing_key=QUEUES["dead"])

    async def close(self) -> None:
        self._closing = True
        if self._channel is not None:
            await self._channel.close()
        if self._connection is not None:
            await self._connection.close()

    def _on_connection_close(self, sender: Any, exc: BaseException | None = None) -> None:
        if self._closing:
            return
        logger.error("[rabbit] connection closed unexpectedly, exiting")
        self._process_exit(1)

    def _on_channel_close(self, sender: Any, exc: BaseException | None = None) -> None:
        if exc is not None:
            logger.error("[rabbit] channel error: %s", exc)

    async def publish(
        self, queue: str, payload: Any, headers: dict[str, Any] | None = None
    ) -> None:
        exchange = self._must_exchange()
        message = aio_pika.Message(
            body=json.dumps(payload).encode("utf-8"),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers=headers or {},
        )
        await exchange.publish(message, routing_key=queue)

    async def consume(self, queue_name: str, handler: JobHandler) -> None:
        channel = self._must_channel()
        queue = await channel.get_queue(queue_name)

        async def on_message(message: AbstractIncomingMessage) -> None:
            attempt = int((message.headers or {}).get("x-attempt") or 0)
            raw = message.body.decode("utf-8")

            try:
                job = json.loads(raw)
                await handler(job, message)
                await message.ack()
            except Exception as error:  # noqa: BLE001 -- mirrors TS's catch-all retry/DLX path
                reason = str(error)
                logger.error("[rabbit] %s failed attempt=%s: %s", queue_name, attempt, reason)

                # Re-parse the original pristine bytes for republish, rather
                # than reusing `job` -- if the handler mutated it in place,
                # those mutations must not leak into the retried/dead copy.
                if attempt < config.rabbit.max_retries:
                    await self.publish(
                        f"{queue_name}.retry",
                        json.loads(raw),
                        {"x-attempt": attempt + 1, "x-error": reason[:1000]},
                    )
                else:
                    await self.publish(
                        QUEUES["dead"],
                        {
                            "queue": queue_name,
                            "payload": json.loads(raw),
                            "error": reason,
                            "attempts": attempt,
                            "failedAt": datetime.now(UTC).isoformat(),
                        },
                    )

                # Either way, ack the original delivery -- failures never
                # nack/requeue it; the retry mechanism is "ack the failed
                # one, publish a fresh copy elsewhere".
                await message.ack()

        await queue.consume(on_message)

    async def _assert_main_and_retry_queue(self, queue_name: str) -> None:
        channel = self._must_channel()
        exchange = self._must_exchange()

        main_queue = await channel.declare_queue(queue_name, durable=True)
        await main_queue.bind(exchange, routing_key=queue_name)

        retry_queue_name = f"{queue_name}.retry"
        retry_queue = await channel.declare_queue(
            retry_queue_name,
            durable=True,
            arguments={
                "x-message-ttl": int(config.rabbit.retry_delay_ms),
                "x-dead-letter-exchange": config.rabbit.exchange,
                "x-dead-letter-routing-key": queue_name,
            },
        )
        await retry_queue.bind(exchange, routing_key=retry_queue_name)

    def _must_channel(self) -> AbstractChannel:
        if self._channel is None:
            raise RuntimeError("RabbitMQ is not connected")
        return self._channel

    def _must_exchange(self) -> AbstractExchange:
        if self._exchange is None:
            raise RuntimeError("RabbitMQ is not connected")
        return self._exchange
