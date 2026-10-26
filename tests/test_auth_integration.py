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
            return response.json() if response.status_code != 204 else None

        request("GET", "/api/auth/public-key")
        request("GET", "/api/auth/token", expected=401)
        request("GET", "/api/auth/token", auth=("admin", "incorrect"), expected=401)
        request("GET", "/api/orders", expected=401)
        request("GET", "/api/payments/1/balance", expected=401)
        request("GET", "/api/machine/status", expected=401)
        request("GET", "/api/deliveries", expected=401)
        request("GET", "/api/machine/status", headers={"Authorization": "Bearer invalid"}, expected=401)
        request("GET", "/api/deliveries", headers={"Authorization": "Bearer invalid"}, expected=401)
        request("GET", "/api/orders", headers={"Authorization": "Bearer invalid"}, expected=401)
        for method, path, body in (("GET", "/api/clients", None), ("GET", "/api/clients/1", None),
                                   ("POST", "/api/clients", {"name": "Anonymous", "email": "anonymous@example.org"})):
            request(method, path, json=body, expected=401)
            request(method, path, json=body, headers={"Authorization": "Bearer invalid"}, expected=401)
        request("POST", "/api/auth/token", expected=401)
        request("POST", "/api/auth/token", auth=("admin", "incorrect"), expected=401)
        admin_token = request("POST", "/api/auth/token", auth=(
            os.getenv("TEST_AUTH_USERNAME", "admin"), os.getenv("TEST_AUTH_PASSWORD", "admin123")))
        admin_headers = {"Authorization": f"Bearer {admin_token['access_token']}"}

        # Offset client/user IDs to catch accidental ownership by unrelated IDs.
        for _ in range(2):
            other = request("POST", "/api/clients", headers=admin_headers, expected=201, json={
                "name": "Auth regression", "email": f"offset-{uuid.uuid4().hex}@example.org",
            })
        identity = uuid.uuid4().hex
        email = f"auth-{identity}@example.org"
        customer = request("POST", "/api/clients", headers=admin_headers, expected=201,
                           json={"name": "Usuario de prueba auth", "email": email})
        user = {"username": f"test-{identity}", "email": email, "password": uuid.uuid4().hex}
        request("POST", "/api/auth/register", json={**user, "roles": ["admin"]}, expected=400)
        request("POST", "/api/auth/register", json=user, expected=201)
        request("POST", "/api/auth/register", json=user, expected=400)
        token = request("POST", "/api/auth/token", auth=(user["username"], user["password"]))
        renewed = request("POST", "/api/auth/refresh", json={"refresh_token": token["refresh_token"]})
        assert renewed["refresh_token"] != token["refresh_token"]
        headers = {"Authorization": f"Bearer {renewed['access_token']}"}
        request("GET", f"/api/clients/{customer['id']}", headers=headers)
        request("GET", f"/api/clients/{other['id']}", headers=headers, expected=403)
        own_clients = request("GET", "/api/clients", headers=headers)
        assert [row['id'] for row in own_clients] == [customer['id']], own_clients
        request("POST", "/api/clients", headers=headers, expected=403,
                json={"name": "Other", "email": f"other-{identity}@example.org"})
        request("GET", "/api/machine/status", headers=headers)
        request("GET", "/api/deliveries", headers=headers)
        request("GET", f"/api/payments/{customer['id']}/balance", headers=headers)
        request("POST", f"/api/payments/{customer['id']}/deposit", headers=headers,
                json={"amount": 10}, expected=201)
        request("GET", "/api/payments/1/balance", headers=headers, expected=403)
        request("POST", "/api/orders", headers=headers, expected=403,
                json={"client_id": 1, "number_of_pieces": 1, "address": "Madrid"})
        # Reuse of a consumed refresh revokes the entire login session.
        request("POST", "/api/auth/refresh", json={"refresh_token": token["refresh_token"]}, expected=401)
        request("POST", "/api/auth/refresh", json={"refresh_token": renewed["refresh_token"]}, expected=401)
        logout_session = request("POST", "/api/auth/token", auth=(user["username"], user["password"]))
        request("POST", "/api/auth/logout", json={"refresh_token": logout_session["refresh_token"]}, expected=204)
        request("POST", "/api/auth/refresh", json={"refresh_token": logout_session["refresh_token"]}, expected=401)
        print("PASS auth: login, refresh rotation/replay/logout, client permissions, machine/delivery JWT", flush=True)


if __name__ == "__main__":
    run()
