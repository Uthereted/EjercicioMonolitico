from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
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


async def create_user(db: AsyncSession, user_schema: schemas.UserCreate):
    roles = []
    for r_name in user_schema.roles:
        res = await db.execute(select(models.Role).where(models.Role.name == r_name))
        r_obj = res.scalar_one_or_none()
        if r_obj:
            roles.append(r_obj)

    db_user = models.User(
        username=user_schema.username,
        email=user_schema.email,
        password_hash=crypto.hash_password(user_schema.password),
        roles=roles
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


async def create_client(db: AsyncSession, client: schemas.ClientCreate):
    db_client = models.Client(name=client.name, email=client.email)
    db.add(db_client)
    await db.flush()
    await record_event(db, "client.created", {"client_id": db_client.id}, f"client.created:{db_client.id}")
    await db.commit()
    await db.refresh(db_client)
    return db_client


async def get_client(db: AsyncSession, client_id: int):
    return await db.get(models.Client, client_id)


async def get_client_by_email(db: AsyncSession, email: str):
    return await db.scalar(select(models.Client).where(models.Client.email == email))


async def get_clients(db: AsyncSession):
    result = await db.execute(select(models.Client))
    return result.scalars().all()
