from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select, update
from app import crypto
from . import models, schemas
from app.events import record_event


async def seed_initial_data(db: AsyncSession):
    # 1. Crear roles por defecto
    for role_name in ["admin", "user"]:
        result = await db.execute(select(models.Role).where(models.Role.name == role_name))
        if not result.scalar_one_or_none():
            db.add(models.Role(name=role_name))
    await db.commit()

    # 2. Crear usuario 'admin' inicial
    result = await db.execute(select(models.User).where(models.User.username == "admin"))
    if not result.unique().scalar_one_or_none():
        admin_role_res = await db.execute(select(models.Role).where(models.Role.name == "admin"))
        admin_role = admin_role_res.scalar_one()

        db.add(models.User(
            username="admin",
            email="admin@manufacturing.org",
            password_hash=crypto.hash_password("admin123"),
            roles=[admin_role]
        ))
        await db.commit()


async def get_user_by_username(db: AsyncSession, username: str):
    result = await db.execute(select(models.User).where(models.User.username == username))
    return result.unique().scalar_one_or_none()


async def register_client(db: AsyncSession, registration: schemas.ClientRegistration):
    """Create credentials, client profile and its event in one transaction."""
    roles = []
    for r_name in registration.roles:
        res = await db.execute(select(models.Role).where(models.Role.name == r_name))
        r_obj = res.scalar_one_or_none()
        if r_obj:
            roles.append(r_obj)

    db_user = models.User(
        username=registration.username,
        email=registration.email,
        password_hash=crypto.hash_password(registration.password),
        roles=roles
    )
    db.add(db_user)
    db_client = models.Client(**registration.model_dump(include={'name', 'email', 'address', 'postal_code'}))
    db.add(db_client)
    await db.flush()
    await record_event(db, "client.created", {
        "client_id": db_client.id, "address": db_client.address, "postal_code": db_client.postal_code,
    }, f"client.created:{db_client.id}")
    await db.commit()
    await db.refresh(db_client)
    return db_client


async def get_client(db: AsyncSession, client_id: int):
    return await db.scalar(select(models.Client).where(
        models.Client.id == client_id, models.Client.deleted.is_(False)))


async def get_client_by_email(db: AsyncSession, email: str, *, include_deleted=False):
    query = select(models.Client).where(models.Client.email == email)
    if not include_deleted:
        query = query.where(models.Client.deleted.is_(False))
    return await db.scalar(query)


async def get_client_profiles(db: AsyncSession, *, client_id=None):
    query = select(models.Client, models.User.username).outerjoin(
        models.User, models.User.email == models.Client.email).where(models.Client.deleted.is_(False))
    if client_id is not None:
        query = query.where(models.Client.id == client_id)
    rows = (await db.execute(query)).all()
    return [schemas.ClientResponse(
        **schemas.ClientResponse.model_validate(client).model_dump(exclude={'username'}), username=username,
    ) for client, username in rows]


async def delete_client(db: AsyncSession, client: models.Client):
    client.deleted = True
    await db.execute(update(models.RefreshToken).where(models.RefreshToken.user_id.in_(
        select(models.User.id).where(models.User.email == client.email)
    )).values(revoked_at=func.now()))
    await record_event(db, "client.deleted", {"client_id": client.id}, f"client.deleted:{client.id}")
    await db.commit()
