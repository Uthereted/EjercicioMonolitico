"""Live regression checks for the merged client/auth endpoints and JWT APIs."""
import os
import uuid

import httpx


def run():
    base_url = os.getenv("API_BASE_URL", "http://localhost:8080")
    with httpx.Client(base_url=base_url, timeout=10) as client:
        def request(method, path, expected=200, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == expected, (path, response.status_code, response.text)
            return response.json()

        request("GET", "/api/auth/public-key")
        request("GET", "/api/auth/token", expected=401)
        request("GET", "/api/auth/token", auth=("admin", "incorrect"), expected=401)
        request("GET", "/api/orders", expected=401)
        request("GET", "/api/payments/1/balance", expected=401)
        request("GET", "/api/orders", headers={"Authorization": "Bearer invalid"}, expected=401)

        # Offset client/user IDs to catch accidental ownership by unrelated IDs.
        for _ in range(2):
            request("POST", "/api/clients", expected=201, json={
                "name": "Auth regression", "email": f"offset-{uuid.uuid4().hex}@example.org",
            })
        identity = uuid.uuid4().hex
        email = f"auth-{identity}@example.org"
        customer = request("POST", "/api/clients", expected=201,
                           json={"name": "Usuario de prueba auth", "email": email})
        user = {"username": f"test-{identity}", "email": email, "password": uuid.uuid4().hex}
        request("POST", "/api/auth/register", json={**user, "roles": ["admin"]}, expected=400)
        request("POST", "/api/auth/register", json=user, expected=201)
        request("POST", "/api/auth/register", json=user, expected=400)
        token = request("GET", "/api/auth/token", auth=(user["username"], user["password"]))
        headers = {"Authorization": f"Bearer {token['access_token']}"}
        request("GET", f"/api/payments/{customer['id']}/balance", headers=headers)
        request("POST", f"/api/payments/{customer['id']}/deposit", headers=headers,
                json={"amount": 10}, expected=201)
        request("GET", "/api/payments/1/balance", headers=headers, expected=403)
        request("POST", "/api/orders", headers=headers, expected=403,
                json={"client_id": 1, "number_of_pieces": 1, "address": "Madrid"})
        print("PASS auth: unique client route, Basic login, register, JWT rejection and client ownership", flush=True)


if __name__ == "__main__":
    run()
