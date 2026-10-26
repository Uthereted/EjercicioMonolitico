"""Persistent, rotating refresh sessions; raw refresh tokens are never stored."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from . import crypto
from .sql import crud, models, schemas

REFRESH_LIFETIME = timedelta(days=7)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def issue_tokens(db, user, *, family_id=None, expires_at=None):
    """Stage a token pair in the caller's transaction; preserve family expiry."""
    now = utc_now()
    expires_at = expires_at if expires_at is not None else now + REFRESH_LIFETIME
    refresh_token = secrets.token_urlsafe(48)
    client = await crud.get_client_by_email(db, user.email)
    access_token = crypto.create_jwt_token({
        "sub": str(user.id), "username": user.username, "email": user.email,
        "roles": [role.name for role in user.roles],
        "client_id": client.id if client else None, "jti": uuid4().hex,
    })
    db.add(models.RefreshToken(
        token_hash=token_hash(refresh_token), family_id=family_id or str(uuid4()),
        user_id=user.id, expires_at=expires_at,
    ))
    return schemas.UserTokenResponse(
        access_token=access_token, refresh_token=refresh_token,
        refresh_expires_in=max(0, int((expires_at - now).total_seconds())),
    )


async def revoke_family(db, family_id):
    await db.execute(update(models.RefreshToken).where(
        models.RefreshToken.family_id == family_id,
        models.RefreshToken.revoked_at.is_(None),
    ).values(revoked_at=utc_now()).execution_options(synchronize_session=False))


async def rotate_tokens(db, refresh_token):
    fingerprint = token_hash(refresh_token)
    now = utc_now()
    # Claim the token atomically: parallel requests cannot both rotate it.
    result = await db.execute(update(models.RefreshToken).where(
        models.RefreshToken.token_hash == fingerprint,
        models.RefreshToken.consumed_at.is_(None),
        models.RefreshToken.revoked_at.is_(None),
        models.RefreshToken.expires_at > now,
    ).values(consumed_at=now).returning(
        models.RefreshToken.user_id, models.RefreshToken.family_id, models.RefreshToken.expires_at,
    ).execution_options(synchronize_session=False))
    claimed = result.first()
    if claimed is None:
        previous = await db.get(models.RefreshToken, fingerprint)
        if previous is not None and previous.consumed_at is not None:
            await revoke_family(db, previous.family_id)
        await db.commit()
        raise HTTPException(401, "Invalid or expired refresh token")

    user = (await db.execute(select(models.User).where(
        models.User.id == claimed.user_id,
    ))).unique().scalar_one_or_none()
    if user is None:
        await revoke_family(db, claimed.family_id)
        await db.commit()
        raise HTTPException(401, "Invalid or expired refresh token")
    tokens = await issue_tokens(db, user, family_id=claimed.family_id, expires_at=claimed.expires_at)
    await db.commit()
    return tokens


async def logout(db, refresh_token):
    previous = await db.get(models.RefreshToken, token_hash(refresh_token))
    if previous is not None:
        await revoke_family(db, previous.family_id)
    await db.commit()
