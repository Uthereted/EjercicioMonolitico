"""Live saga test: successful order and payment/delivery rejection compensation.

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
        def request(method, path, expected=None, **kwargs):
            deadline = time.monotonic() + 10
            while True:
                response = client.request(method, path, **kwargs)
                catalogue_pending = (
                    response.status_code == 404 and (path.endswith(("/deposit", "/balance")) or path == "/api/orders")
                    or response.status_code == 409 and path == "/api/orders"
                    and response.json().get("detail") == "Client address is not present in the local catalogue yet"
                )
                if not (catalogue_pending and time.monotonic() < deadline):
                    break
                time.sleep(0.2)
            response.raise_for_status()
            if expected is not None:
                assert response.status_code == expected, response.text
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
            "address": "Calle Mayor 10", "postal_code": "01001",
        })
        customer_id = customer["id"]
        request("POST", f"/api/payments/{customer_id}/deposit", json={"amount": 100})
        created = request("POST", "/api/orders", expected=202, json={
            "client_id": customer_id, "number_of_pieces": 2,
            "description": "Verificacion saga machine/order",
        })
        assert created["status"] == "PendingPayment", created
        assert created["address"] == "Calle Mayor 10, 01001", created
        order_id = created["id"]
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
            assert order["status"] not in ("Rejected", "Compensating", "Suspended"), order
            time.sleep(1)
        else:
            raise AssertionError(f"Manufacturing timed out: {order}")
        assert order["payment_completed"], order
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

        for scenario, funds, postal_code in (("no-funds", 0, "01001"), ("outside-coverage", 100, "28001")):
            customer = request("POST", "/api/clients", expected=201, json={
                "name": scenario, "email": f"{scenario}-{uuid.uuid4().hex}@example.org",
                "address": "Calle Mayor 10", "postal_code": postal_code,
            })
            customer_id = customer["id"]
            if funds:
                request("POST", f"/api/payments/{customer_id}/deposit", json={"amount": funds})
            created = request("POST", "/api/orders", expected=202, json={
                "client_id": customer_id, "number_of_pieces": 1, "description": scenario,
            })
            order_id = created["id"]
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                order = request("GET", f"/api/orders/{order_id}")
                if order["status"] == "Suspended":
                    break
                assert not order["payment_completed"], order
                time.sleep(0.25)
            assert order["status"] == "Suspended", order
            assert not order["payment_completed"] and order["integration_error"], order
            pieces = request("GET", f"/api/orders/{order_id}/pieces")
            assert len(pieces) == 1 and pieces[0]["status"] == "Queued", pieces
            delivery = request("GET", f"/api/deliveries/{order['delivery_id']}")
            assert delivery["status"] == "Canceled", delivery
            balance = request("GET", f"/api/payments/{customer_id}/balance")
            assert balance["balance"] == funds, balance
            print(f"PASS {scenario}: order={order_id}, Suspended, delivery=Canceled, balance={funds}", flush=True)


if __name__ == "__main__":
    run()
