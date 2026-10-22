from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.dependencies import get_db
from app.sql import crud, schemas
from app import crypto

router = APIRouter()
security_basic = HTTPBasic()


@router.get("/auth/public-key", tags=["Auth"])
async def get_public_key():
    """Devuelve la clave pública RSA actual en formato PEM."""
    return {"public_key": crypto.get_public_key_pem()}


@router.get("/auth/token", response_model=schemas.TokenResponse, tags=["Auth"])
async def login_basic_auth(
        credentials: HTTPBasicCredentials = Depends(security_basic),
        db: AsyncSession = Depends(get_db)
):
    """Basic Auth (username:password). Devuelve JWT firmado con la clave privada RS256."""
    user = await crud.get_user_by_username(db, credentials.username)
    if not user or not crypto.verify_password(credentials.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )

    token_payload = {
        "sub": str(user.id),
        "username": user.username,
        "email": user.email,
        "roles": [r.name for r in user.roles]
    }
    jwt_token = crypto.create_jwt_token(token_payload)
    return schemas.TokenResponse(access_token=jwt_token)


@router.post("/auth/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED, tags=["Auth"])
async def register_user(user_data: schemas.UserCreate, db: AsyncSession = Depends(get_db)):
    """Registra un nuevo usuario."""
    existing_user = await crud.get_user_by_username(db, user_data.username)
    if existing_user:
        raise HTTPException(status_code=400, detail="El usuario ya existe")
    return await crud.create_user(db, user_data)


@router.post("/clients", response_model=schemas.ClientResponse, status_code=status.HTTP_201_CREATED, tags=["Client"])
async def create_client(client: schemas.ClientCreate, db: AsyncSession = Depends(get_db)):
    return await crud.create_client(db, client)


@router.get("/clients/{client_id}", response_model=schemas.ClientResponse, tags=["Client"])
async def read_client(client_id: int, db: AsyncSession = Depends(get_db)):
    db_client = await crud.get_client(db, client_id)
    if not db_client:
        raise HTTPException(status_code=404, detail="Client not found")
    return db_client


@router.get("/clients", response_model=List[schemas.ClientResponse], tags=["Client"])
async def read_clients(db: AsyncSession = Depends(get_db)):
    return await crud.get_clients(db)