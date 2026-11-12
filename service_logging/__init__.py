"""Application logs on their own RabbitMQ exchange, with fail-closed operation gates."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
import json
import logging
import os
import threading
import time
from uuid import uuid4

import aio_pika
import httpx
from my_chassis.events import EventBus
from starlette.responses import JSONResponse

LOG_EXCHANGE = 'manufacturing.logs'
UNAVAILABLE = 'Log aggregation unavailable; operations are paused'


class LogUnavailable(Exception):
    pass


class RabbitLogHandler(logging.Handler):
    """Capture application logging without blocking the event loop or using stdout."""

    def emit(self, record):
        # Transport diagnostics would recursively generate logs while sending logs.
        if record.name.startswith(('aio_pika', 'aiormq', 'httpx', 'httpcore',
                                   'pymongo', 'asyncio', 'aiosqlite', 'uvicorn.access', 'hypercorn.access')):
            return
        entry = {
            'log_id': uuid4().hex,
            'timestamp': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            'service': runtime.service,
            'level': record.levelname,
            'message': record.getMessage(),
            'logger': record.name,
            'file': record.pathname,
            'line': record.lineno,
            'exception': (logging.Formatter().formatException(record.exc_info)
                          if record.exc_info else None),
        }
        with runtime.pending_lock:
            runtime.pending.append(entry)


class LogRuntime:
    def __init__(self):
        self.service = None
        self.pending = deque()
        self.pending_lock = threading.Lock()
        self.ready = False
        self.checked_at = 0
        self.connection = None
        self.channel = None
        self.exchange = None
        self.http = None
        self.lock = None

    @property
    def available(self):
        return (self.ready and time.monotonic() - self.checked_at < 4
                and self.connection is not None and not self.connection.is_closed
                and self.channel is not None and not self.channel.is_closed)

    async def connect(self):
        if self.connection is None or self.connection.is_closed:
            self.channel = None
            self.connection = await aio_pika.connect(
                os.getenv('RABBITMQ_URL', 'amqp://manufacturing:manufacturing@rabbitmq:5672/'),
                timeout=2,
            )
        if self.channel is None or self.channel.is_closed:
            self.channel = await self.connection.channel(
                publisher_confirms=True, on_return_raises=True)
            self.exchange = await self.channel.declare_exchange(
                LOG_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    async def flush(self):
        while True:
            with self.pending_lock:
                entry = self.pending[0] if self.pending else None
            if entry is None:
                return
            routing_key = f"{entry['service']}.{entry['level'].lower()}"
            await self.exchange.publish(aio_pika.Message(
                body=json.dumps(entry).encode(), content_type='application/json',
                message_id=entry['log_id'], type=routing_key,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ), routing_key=routing_key, mandatory=True, timeout=2)
            with self.pending_lock:
                self.pending.popleft()

    async def checkpoint(self, message=None):
        """Check the collector and flush logs before admitting a new operation."""
        if self.lock is None:
            raise LogUnavailable(UNAVAILABLE)
        async with self.lock:
            try:
                response = await self.http.get('/service/health')
                response.raise_for_status()
                await self.connect()
                if message:
                    logging.getLogger('application').info(message)
                await self.flush()
            except Exception as exc:
                self.ready = False
                raise LogUnavailable(UNAVAILABLE) from exc
            self.checked_at = time.monotonic()
            self.ready = True

    async def wait_available(self, message=None):
        """Pause background operations, retrying without committing or acknowledging."""
        while True:
            try:
                await self.checkpoint(message)
                return
            except LogUnavailable:
                await asyncio.sleep(1)

    async def monitor(self):
        while True:
            try:
                await self.checkpoint()
            except LogUnavailable:
                pass
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(self):
        self.lock = asyncio.Lock()
        self.http = httpx.AsyncClient(
            base_url=os.getenv('LOG_AGGREGATOR_URL', 'http://log-aggregator:8000'),
            timeout=2, trust_env=False,
        )
        logging.getLogger('application').info('Service starting')
        task = asyncio.create_task(self.monitor())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            # Best effort to drain memory on shutdown; never fall back to the console.
            with suppress(LogUnavailable):
                await self.checkpoint()
            self.ready = False
            await self.http.aclose()
            if self.connection is not None:
                await self.connection.close()


runtime = LogRuntime()


def install_logging(app, service):
    runtime.service = service
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(RabbitLogHandler())
    root.setLevel(logging.DEBUG)
    logging.lastResort = logging.NullHandler()
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            logger.handlers.clear()
            logger.propagate = True
    for name in ('aio_pika', 'aiormq', 'httpx', 'httpcore', 'pymongo', 'asyncio', 'aiosqlite', 'sqlalchemy'):
        logging.getLogger(name).setLevel(logging.WARNING)

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with runtime.lifespan():
            async with original_lifespan(application):
                yield

    app.router.lifespan_context = lifespan

    @app.middleware('http')
    async def require_logging(request, call_next):
        path = request.url.path
        if path in ('/service/health', '/health', '/deliveries/'):
            if not runtime.available:
                return JSONResponse({'detail': UNAVAILABLE}, status_code=503)
            return await call_next(request)
        try:
            # Deliberately exclude headers, query values and request/response bodies.
            await runtime.checkpoint(f'{request.method} {path}')
        except LogUnavailable:
            return JSONResponse({'detail': UNAVAILABLE}, status_code=503)
        try:
            response = await call_next(request)
        except Exception:
            logging.getLogger('application').exception('Unhandled request exception')
            return JSONResponse({'detail': 'Internal server error'}, status_code=500)
        logger = logging.getLogger('application')
        level = logging.ERROR if response.status_code >= 500 else (
            logging.WARNING if response.status_code >= 400 else logging.INFO)
        logger.log(level, '%s %s returned %s', request.method, path, response.status_code)
        return response


class LoggedEventBus(EventBus):
    """Gate the existing event bus without changing its events or exchanges."""

    async def flush_outbox(self, exchange):
        await runtime.wait_available()
        return await super().flush_outbox(exchange)

    async def dispatch(self, event_id, event_type, data, handler):
        await runtime.wait_available(f'Processing event {event_type}')
        return await super().dispatch(event_id, event_type, data, handler)
