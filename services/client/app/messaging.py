"""Publish the public signing key so JWT consumers can bootstrap and recover."""
import asyncio
import hashlib
from uuid import uuid4
from . import crypto
from .events import record_event
from .sql import database


async def publish_signing_key():
    while True:
        async with database.SessionLocal() as db:
            epoch = uuid4().hex
            pem = crypto.get_public_key_pem()
            fingerprint = hashlib.sha256(pem.encode()).hexdigest()
            await record_event(db, "auth.signing-key.published", {"public_key": pem},
                               f"auth.signing-key.published:{fingerprint}:{epoch}")
            await db.commit()
        await asyncio.sleep(5)
