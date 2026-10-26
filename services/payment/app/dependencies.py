from my_chassis.database import create_db_dependency
import os
import httpx
from .sql.database import SessionLocal

CLIENT_SERVICE_URL = os.getenv("CLIENT_SERVICE_URL", "http://localhost:13005").rstrip("/")


get_db = create_db_dependency(SessionLocal)


async def client_exists(client_id: int) -> bool:
    async with httpx.AsyncClient(timeout=5) as client:
        secret = os.getenv("PAYMENT_SERVICE_AUTH_SECRET", "")
        if not secret:
            raise ValueError("PAYMENT_SERVICE_AUTH_SECRET is not configured")
        token_response = await client.post(f"{CLIENT_SERVICE_URL}/auth/service-token",
                                           auth=httpx.BasicAuth("payment-service", secret))
        token_response.raise_for_status()
        response = await client.get(f"{CLIENT_SERVICE_URL}/clients/{client_id}", headers={
            "Authorization": f"Bearer {token_response.json()['access_token']}",
        })
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True
