from typing import List
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.dependencies import get_db
from app.sql import crud, schemas
from app import crypto, auth_sessions
from app.auth_verifier import verify_jwt, is_admin

router = APIRouter()
security_basic = HTTPBasic()


@router.post("/auth/token", response_model=schemas.UserTokenResponse, tags=["Auth"])
async def login_basic_auth(
        response: Response,
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

    tokens = await auth_sessions.issue_tokens(db, user)
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return tokens


@router.post("/auth/refresh", response_model=schemas.UserTokenResponse, tags=["Auth"])
async def refresh_tokens(body: schemas.RefreshTokenRequest, response: Response,
                         db: AsyncSession = Depends(get_db)):
    tokens = await auth_sessions.rotate_tokens(db, body.refresh_token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return tokens


@router.post("/auth/logout", status_code=204, tags=["Auth"])
async def logout(body: schemas.RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    await auth_sessions.logout(db, body.refresh_token)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/auth/register", response_model=schemas.ClientResponse, status_code=status.HTTP_201_CREATED, tags=["Client"])
async def register_client(registration: schemas.ClientRegistration, db: AsyncSession = Depends(get_db)):
    """Register the client and its login account together; id is the client id."""
    if registration.roles != ["user"]:
        raise HTTPException(status_code=400, detail="El registro público solo permite el rol user")
    try:
        client = await crud.register_client(db, registration)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Username or email already exists")
    return schemas.ClientResponse(
        **schemas.ClientResponse.model_validate(client).model_dump(exclude={'username'}), username=registration.username,
    )


@router.delete("/clients/{client_id}", status_code=204, tags=["Client"])
async def delete_client(client_id: int, db: AsyncSession = Depends(get_db),
                        token_payload: dict = Depends(verify_jwt)):
    if not is_admin(token_payload):
        raise HTTPException(403, "Only administrators can delete clients")
    client = await crud.get_client(db, client_id)
    if client is None:
        raise HTTPException(404, "Client not found")
    await crud.delete_client(db, client)
    return Response(status_code=204)


@router.get("/clients/{client_id}", response_model=schemas.ClientResponse, tags=["Client"])
async def read_client(client_id: int, db: AsyncSession = Depends(get_db),
                      token_payload: dict = Depends(verify_jwt)):
    if not is_admin(token_payload) and token_payload.get("client_id") != client_id:
        raise HTTPException(403, "You can only read your own client")
    profiles = await crud.get_client_profiles(db, client_id=client_id)
    if not profiles:
        raise HTTPException(status_code=404, detail="Client not found")
    return profiles[0]


@router.get("/clients", response_model=List[schemas.ClientResponse], tags=["Client"])
async def read_clients(db: AsyncSession = Depends(get_db), token_payload: dict = Depends(verify_jwt)):
    if is_admin(token_payload):
        return await crud.get_client_profiles(db)
    if token_payload.get("service_name") is not None:
        raise HTTPException(403, "Services cannot list clients")
    client_id = token_payload.get("client_id")
    return await crud.get_client_profiles(db, client_id=client_id) if client_id is not None else []
