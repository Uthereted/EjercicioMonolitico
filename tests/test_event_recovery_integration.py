"""Live recovery test: temporarily stop delivery and restart a working machine.

Requires the local Compose stack. Test records remain; delivery is restored in finally.
"""
import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def compose(*args):
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True, text=True)


def wait_for(check, timeout=90):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except httpx.HTTPError as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise AssertionError(f"Recovery timed out: {last_error}")


def run():
    with httpx.Client(base_url=os.getenv("API_BASE_URL", "http://localhost:8080"), timeout=10) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        token = request("POST", "/api/auth/token", auth=(
            os.getenv("TEST_AUTH_USERNAME", "admin"), os.getenv("TEST_AUTH_PASSWORD", "admin123")))
        client.headers["Authorization"] = f"Bearer {token['access_token']}"
        customer = request("POST", "/api/clients", json={
            "name": "Event recovery", "email": f"recovery-{uuid.uuid4().hex}@example.org"})
        wait_for(lambda: request("POST", f"/api/payments/{customer['id']}/deposit", json={"amount": 100}))
        delivery_stopped = False
        try:
            compose("stop", "delivery")
            delivery_stopped = True
            order = wait_for(lambda: request("POST", "/api/orders", json={
                "client_id": customer["id"], "number_of_pieces": 1, "address": "Madrid"}))
            order_id = order["id"]
            pieces = request("GET", f"/api/orders/{order_id}/pieces")
            piece_id = pieces[0]["id"]
            wait_for(lambda: request("GET", "/api/machine/status")["working_piece"] == piece_id)
            print(f"Order {order_id}: delivery offline, restarting machine during piece {piece_id}", flush=True)
            compose("restart", "machineservice")
            wait_for(lambda: all(p["status"] == "Manufactured" for p in request(
                "GET", f"/api/orders/{order_id}/pieces")))
            order = request("GET", f"/api/orders/{order_id}")
            assert order["delivery_id"] is None, order
            assert order["integration_error"] is None, order
            print("Manufacturing completed while delivery was offline", flush=True)
            compose("start", "delivery")
            delivery_stopped = False
            wait_for(lambda: request("GET", f"/api/orders/{order_id}")["status"] == "ReadyForDelivery")
            order = request("GET", f"/api/orders/{order_id}")
            deliveries = request("GET", "/api/deliveries")
            assert len([d for d in deliveries if d["order_id"] == order_id]) == 1
            request("PATCH", f"/api/deliveries/{order['delivery_id']}/status", json={"status": "Delivered"})
            wait_for(lambda: request("GET", f"/api/orders/{order_id}")["status"] == "Delivered")
            balance = request("GET", f"/api/payments/{customer['id']}/balance")
            assert balance["balance"] == 90, balance
            print(f"PASS recovery: order={order_id}, one delivery, one charge, balance=90", flush=True)
        finally:
            if delivery_stopped:
                compose("start", "delivery")


if __name__ == "__main__":
    run()
