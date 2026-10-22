"""Live smoke test; creates one client, deposits 100 and orders two pieces.

Run against Docker Compose: python tests/test_workflow_integration.py
Requires httpx. API_BASE_URL defaults to http://localhost:8080.
The created records are retained as evidence of the test.
"""
import os
import time
import uuid

import httpx


def run():
    base_url = os.getenv("API_BASE_URL", "http://localhost:8080").rstrip("/")
    with httpx.Client(base_url=base_url, timeout=10) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        token = client.get("/api/auth/token", auth=(
            os.getenv("TEST_AUTH_USERNAME", "admin"),
            os.getenv("TEST_AUTH_PASSWORD", "admin123"),
        ))
        token.raise_for_status()
        client.headers["Authorization"] = f"Bearer {token.json()['access_token']}"

        customer = request("POST", "/api/clients", json={
            "name": "Prueba eventos machine/order",
            "email": f"events-{uuid.uuid4().hex}@example.org",
        })
        customer_id = customer["id"]
        request("POST", f"/api/payments/{customer_id}/deposit", json={"amount": 100})
        response = client.post("/api/orders", json={
            "client_id": customer_id, "number_of_pieces": 2,
            "description": "Verificacion RabbitMQ machine/order", "address": "Madrid",
        })
        response.raise_for_status()
        assert response.status_code == 202, response.text
        order_id = response.json()["id"]
        print(f"Created client={customer_id}, order={order_id}", flush=True)
        deadline = time.monotonic() + 100
        last_status = None
        while time.monotonic() < deadline:
            order = request("GET", f"/api/orders/{order_id}")
            if order["status"] != last_status:
                print(f"Order status: {order['status']}", flush=True)
                last_status = order["status"]
            if order["status"] == "ReadyForDelivery":
                break
            assert order["status"] != "Rejected", order
            time.sleep(1)
        else:
            raise AssertionError(f"Manufacturing timed out: {order}")
        assert order["payment_completed"] and order["manufacturing_requested"], order
        assert order["integration_error"] is None, order
        pieces = request("GET", f"/api/orders/{order_id}/pieces")
        assert len(pieces) == 2 and all(p["status"] == "Manufactured" for p in pieces), pieces
        delivery_id = order["delivery_id"]
        request("PATCH", f"/api/deliveries/{delivery_id}/status", json={
            "status": "Delivered", "tracking_number": f"EVENTS-{order_id}",
        })
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            order = request("GET", f"/api/orders/{order_id}")
            if order["status"] == "Delivered":
                break
            time.sleep(1)
        assert order["status"] == "Delivered", order
        balance = request("GET", f"/api/payments/{customer_id}/balance")
        assert balance["balance"] == 80, balance
        print(f"PASS order={order_id}, delivery={delivery_id}, pieces=Manufactured, status=Delivered, balance=80", flush=True)


if __name__ == "__main__":
    run()
