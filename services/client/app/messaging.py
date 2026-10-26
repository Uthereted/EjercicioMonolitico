"""Publish durable client facts and periodic public snapshots."""
import asyncio
import hashlib
from uuid import uuid4
from sqlalchemy import select
from . import crypto
from .events import record_event
from .sql import database, models


async def publish_catalogue():
    # Snapshots bootstrap new local projections without request/reply.
    while True:
        async with database.SessionLocal() as db:
            ids = list((await db.scalars(select(models.Client.id))).all())
            epoch = uuid4().hex
            await record_event(db, "clients.catalogue.published", {"client_ids": ids},
                               f"clients.catalogue.published:{epoch}")
            pem = crypto.get_public_key_pem()
            fingerprint = hashlib.sha256(pem.encode()).hexdigest()
            await record_event(db, "auth.signing-key.published", {"public_key": pem},
                               f"auth.signing-key.published:{fingerprint}:{epoch}")
            await db.commit()
        await asyncio.sleep(5)
