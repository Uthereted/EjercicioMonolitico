from fastapi import HTTPException
from my_chassis.database import create_db_dependency
from my_chassis.discovery import query_service
from .sql import database

get_db = create_db_dependency(database.SessionLocal)


def check_client_ownership(client_id: int, token_payload: dict):
    """Allow an administrator or the client identified by the signed token."""
    if "admin" in token_payload.get("roles", []):
        return

    # User IDs and client IDs belong to separate tables.
    if token_payload.get("client_id") != client_id:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: No tienes permisos para acceder o modificar los fondos de este cliente"
        )


async def client_exists(client_id: int, access_token: str) -> bool:
    try:
        client = await query_service('client-service', f'/clients/{client_id}', access_token=access_token)
    except HTTPException as exc:
        if exc.status_code == 404:
            return False
        raise
    if not isinstance(client, dict) or client.get('id') != client_id:
        raise HTTPException(502, 'Invalid client query response')
    return True
