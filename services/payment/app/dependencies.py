import os
import httpx
from .sql.database import SessionLocal

CLIENT_SERVICE_URL = os.getenv("CLIENT_SERVICE_URL", "http://localhost:13005").rstrip("/")


async def get_db():
    async with SessionLocal() as db:
        yield db


async def client_exists(client_id: int) -> bool:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"{CLIENT_SERVICE_URL}/clients/{client_id}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True
