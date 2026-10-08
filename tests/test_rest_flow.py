"""Exercise all five real FastAPI apps with HTTP and isolated SQLite databases."""
import asyncio
import importlib
import sqlite3
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def run_scenario(scenario):
    async def bounded():
        async with asyncio.timeout(20):
            await scenario()
    asyncio.run(bounded())


class RoutingTransport(httpx.AsyncBaseTransport):
    def __init__(self, apps):
        self.apps = apps
        self.offline = set()
        self.lost_responses = set()

    async def handle_async_request(self, request):
        host = request.url.host
        key = (host, request.method, request.url.path)
        if host in self.offline or key in self.offline:
            raise httpx.ConnectError("Simulated service outage", request=request)
        transport = httpx.ASGITransport(app=self.apps[host], raise_app_exceptions=False)
        response = await transport.handle_async_request(request)
        if key in self.lost_responses:
            self.lost_responses.remove(key)
            raise httpx.ReadError("Response lost after server commit", request=request)
        return response


@asynccontextmanager
async def cluster(tmp_path, monkeypatch):
    original_client = httpx.AsyncClient
    modules = {}
    apps = {}
    hostnames = {"client": "client_service", "order": "order-service",
                 "payment": "paymentservice", "delivery": "delivery",
                 "machine": "machineservice"}
    for name, host in hostnames.items():
        variable = "ORDERS_SERVICE_URL" if name == "order" else f"{name.upper()}_SERVICE_URL"
        monkeypatch.setenv(variable, f"http://{host}")
    monkeypatch.setenv("REST_RETRY_SECONDS", "0.05")
    monkeypatch.setenv("MANUFACTURING_SECONDS", "0.02")
    saved_path = sys.path[:]
    saved_modules = {name: module for name, module in sys.modules.items()
                     if name == "app" or name.startswith("app.") or name == "main"}
    try:
        for service, host in hostnames.items():
            for name in list(sys.modules):
                if name == "app" or name.startswith("app.") or name == "main":
                    del sys.modules[name]
            sys.path[:] = [str(ROOT / "services" / service)] + saved_path
            monkeypatch.setenv("SQLALCHEMY_DATABASE_URL",
                               f"sqlite+aiosqlite:///{(tmp_path / (service + '.db')).as_posix()}")
            entry = importlib.import_module("main" if service == "machine" else "app.main")
            apps[host] = entry.app
            modules[service] = {name: module for name, module in sys.modules.items()
                                if name == "app" or name.startswith("app.") or name == "main"}
        transport = RoutingTransport(apps)

        def routed_client(*args, **kwargs):
            kwargs.setdefault("transport", transport)
            return original_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", routed_client)
        async with AsyncExitStack() as stack:
            for host in ("client_service", "paymentservice", "order-service", "delivery", "machineservice"):
                await stack.enter_async_context(apps[host].router.lifespan_context(apps[host]))
            async with original_client(transport=transport) as client:
                yield client, transport, modules
    finally:
        monkeypatch.setattr(httpx, "AsyncClient", original_client)
        for name in list(sys.modules):
            if name == "app" or name.startswith("app.") or name == "main":
                del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


async def wait_for(client, url, predicate):
    deadline = asyncio.get_running_loop().time() + 8
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(url)
        assert response.status_code == 200, response.text
        payload = response.json()
        if predicate(payload):
            return payload
        await asyncio.sleep(0.03)
    raise AssertionError(f"Timed out waiting for {url}: {payload}")


async def create_client(client, balance=100):
    response = await client.post("http://client_service/clients",
                                 json={"name": "Test client", "email": "rest@example.org"})
    assert response.status_code == 201, response.text
    client_id = response.json()["id"]
    if balance:
        response = await client.post(f"http://paymentservice/payment/{client_id}/deposit",
                                     json={"amount": balance})
        assert response.status_code == 201, response.text
    return client_id


def order_body(client_id, quantity=3):
    return {"client_id": client_id, "number_of_pieces": quantity,
            "description": "REST integration test", "address": "Calle Mayor 10"}


def test_complete_flow(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            response = await client.post("http://order-service/order", json=order_body(client_id))
            assert response.status_code == 201, response.text
            order = response.json()
            assert order["payment_completed"] and order["total_price"] == 30
            ready = await wait_for(client, f"http://order-service/order/{order['id']}",
                                   lambda body: body["status"] == "ReadyForDelivery")
            pieces = (await client.get(f"http://order-service/order/{order['id']}/pieces")).json()
            assert len(pieces) == 3 and all(piece["status"] == "Manufactured" for piece in pieces)
            balance = (await client.get(f"http://paymentservice/payment/{client_id}/balance")).json()
            assert balance["balance"] == 70
            delivery_url = f"http://delivery/deliveries/delivery/{ready['delivery_id']}"
            response = await client.patch(delivery_url + "/status",
                                          json={"status": "Delivered", "tracking_number": "TRACK-1"})
            assert response.status_code == 200, response.text
            assert response.json()["order_notified"]
            delivered = (await client.get(f"http://order-service/order/{order['id']}")).json()
            assert delivered["status"] == "Delivered"
            await client.patch(delivery_url + "/status", json={"status": "Ready"})
            assert (await client.get(delivery_url)).json()["status"] == "Delivered"
            assert (await client.delete(f"http://order-service/order/{order['id']}")).status_code == 409
    run_scenario(scenario)


def test_rejected_clients_funds_and_invalid_orders(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            assert (await client.post("http://order-service/order", json=order_body(999))).status_code == 404
            client_id = await create_client(client, balance=0)
            response = await client.post("http://order-service/order", json=order_body(client_id))
            assert response.status_code == 409, response.text
            orders = (await client.get("http://order-service/order")).json()
            assert len(orders) == 1 and orders[0]["status"] == "Rejected"
            assert (await client.get("http://order-service/piece/by-status/Queued")).json() == []
            assert (await client.get("http://delivery/deliveries/delivery")).json() == []
            assert (await client.post("http://order-service/order", json=order_body(client_id, 0))).status_code == 422
            assert (await client.post("http://order-service/order", json=order_body(client_id, -1))).status_code == 422
    run_scenario(scenario)


def test_lost_charge_response_is_not_charged_twice(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            transport.lost_responses.add(("paymentservice", "POST", f"/payment/{client_id}/charge"))
            response = await client.post("http://order-service/order", json=order_body(client_id))
            assert response.status_code == 202, response.text
            order_id = response.json()["id"]
            await wait_for(client, f"http://order-service/order/{order_id}",
                           lambda body: body["status"] == "ReadyForDelivery")
            response = await client.post(f"http://paymentservice/payment/{client_id}/charge",
                                         json={"amount": 30, "order_id": order_id})
            assert response.status_code == 200, response.text
            account = response.json()
            assert account["balance"] == 70
            assert len([item for item in account["transactions"] if item["type"] == "Charge"]) == 1
            conflict = await client.post(f"http://paymentservice/payment/{client_id}/charge",
                                         json={"amount": 40, "order_id": order_id})
            assert conflict.status_code == 409
    run_scenario(scenario)


def test_delivery_outage_after_payment_recovers(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            transport.offline.add("delivery")
            response = await client.post("http://order-service/order", json=order_body(client_id))
            assert response.status_code == 201, response.text
            order = response.json()
            assert order["status"] == "Pending" and order["payment_completed"]
            transport.offline.remove("delivery")
            await wait_for(client, f"http://order-service/order/{order['id']}",
                           lambda body: body["status"] == "ReadyForDelivery")
            assert len((await client.get("http://delivery/deliveries/delivery")).json()) == 1
            assert (await client.get(f"http://paymentservice/payment/{client_id}/balance")).json()["balance"] == 70
    run_scenario(scenario)


def test_lost_delivery_response_does_not_create_duplicates(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            transport.lost_responses.add(("delivery", "POST", "/deliveries/delivery"))
            response = await client.post("http://order-service/order", json=order_body(client_id))
            assert response.status_code == 201, response.text
            await wait_for(client, f"http://order-service/order/{response.json()['id']}",
                           lambda body: body["status"] == "ReadyForDelivery")
            assert len((await client.get("http://delivery/deliveries/delivery")).json()) == 1
    run_scenario(scenario)


def test_delivery_callback_recovers_after_order_outage(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            response = await client.post("http://order-service/order", json=order_body(client_id, 1))
            order_id = response.json()["id"]
            order = await wait_for(client, f"http://order-service/order/{order_id}",
                                   lambda body: body["status"] == "ReadyForDelivery")
            transport.offline.add(("order-service", "PATCH", f"/order/{order_id}/status"))
            delivery_url = f"http://delivery/deliveries/delivery/{order['delivery_id']}"
            response = await client.patch(delivery_url + "/status", json={"status": "Delivered"})
            assert response.status_code == 200 and not response.json()["order_notified"]
            transport.offline.clear()
            await wait_for(client, f"http://order-service/order/{order_id}",
                           lambda body: body["status"] == "Delivered")
            await wait_for(client, delivery_url, lambda body: body["order_notified"])
    run_scenario(scenario)


def test_existing_orders_are_migrated_without_being_manufactured(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / "order.db") as db:
        db.execute("""CREATE TABLE manufacturing_order (
            id INTEGER PRIMARY KEY, number_of_pieces INTEGER NOT NULL,
            description TEXT NOT NULL, status VARCHAR(256) NOT NULL,
            creation_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            update_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("INSERT INTO manufacturing_order (id,number_of_pieces,description,status) VALUES (1,2,'Legacy','Created')")
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            order = (await client.get("http://order-service/order/1")).json()
            assert order["description"] == "Legacy" and order["client_id"] is None
            assert (await client.get("http://order-service/piece/by-status/Queued")).json() == []
            client_id = await create_client(client)
            response = await client.post("http://order-service/order", json=order_body(client_id, 1))
            assert response.status_code == 201, response.text
            assert response.json()["id"] == 2
    run_scenario(scenario)


def test_concurrent_charges_are_atomic_and_idempotent(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            url = f"http://paymentservice/payment/{client_id}/charge"
            results = await asyncio.gather(*[
                client.post(url, json={"amount": 80, "order_id": 100}) for _ in range(3)
            ])
            assert all(response.status_code == 200 for response in results), [response.text for response in results]
            assert (await client.get(f"http://paymentservice/payment/{client_id}/balance")).json()["balance"] == 20
            response = await client.post(url, json={"amount": 30, "order_id": 101})
            assert response.status_code == 409
    run_scenario(scenario)


def test_pending_workflow_recovers_after_service_restart(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            transport.offline.add("delivery")
            response = await client.post("http://order-service/order", json=order_body(client_id, 1))
            assert response.status_code == 201
            order_id = response.json()["id"]
            await wait_for(client, f"http://order-service/order/{order_id}/pieces",
                           lambda pieces: all(piece["status"] == "Manufactured" for piece in pieces))
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            order = await wait_for(client, f"http://order-service/order/{order_id}",
                                   lambda body: body["status"] == "ReadyForDelivery")
            assert order["delivery_id"] is not None
            assert (await client.get(f"http://paymentservice/payment/{client_id}/balance")).json()["balance"] == 90
    run_scenario(scenario)


def test_machine_restart_recovers_paid_pieces(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            dependency = modules["machine"]["app.dependencies"]
            await dependency.stop_machine()
            transport.offline.add("machineservice")
            response = await client.post("http://order-service/order", json=order_body(client_id, 2))
            assert response.status_code == 201 and response.json()["status"] == "Pending"
            transport.offline.remove("machineservice")
            await dependency.get_machine()
            await wait_for(client, f"http://order-service/order/{response.json()['id']}",
                           lambda body: body["status"] == "ReadyForDelivery")
            assert (await client.get(f"http://paymentservice/payment/{client_id}/balance")).json()["balance"] == 80
    run_scenario(scenario)


def test_unconfirmed_payment_cannot_start_manufacturing(tmp_path, monkeypatch):
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            client_id = await create_client(client)
            transport.offline.add("paymentservice")
            response = await client.post("http://order-service/order", json=order_body(client_id, 1))
            assert response.status_code == 202 and response.json()["status"] == "PendingPayment"
            assert (await client.get("http://order-service/piece/by-status/Queued")).json() == []
            assert (await client.get("http://delivery/deliveries/delivery")).json() == []
            piece_id = (await client.get(f"http://order-service/order/{response.json()['id']}/pieces")).json()[0]["id"]
            assert (await client.patch(f"http://order-service/piece/{piece_id}",
                                      json={"status": "Manufacturing"})).status_code == 409
            transport.offline.remove("paymentservice")
            await wait_for(client, f"http://order-service/order/{response.json()['id']}",
                           lambda body: body["status"] == "ReadyForDelivery")
    run_scenario(scenario)


def test_legacy_duplicate_deliveries_are_preserved(tmp_path, monkeypatch):
    with sqlite3.connect(tmp_path / "delivery.db") as db:
        db.execute("""CREATE TABLE delivery (
            id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
            address VARCHAR(500) NOT NULL, status VARCHAR(50) NOT NULL,
            tracking_number VARCHAR(100), creation_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            update_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.executemany("INSERT INTO delivery (client_id,order_id,address,status) VALUES (1,1,?, 'Delivered')",
                       [("Legacy address A",), ("Legacy address B",)])
    async def scenario():
        async with cluster(tmp_path, monkeypatch) as (client, transport, modules):
            assert len((await client.get("http://delivery/deliveries/delivery")).json()) == 2
            client_id = await create_client(client)
            response = await client.post("http://order-service/order", json=order_body(client_id, 1))
            assert response.status_code == 201, response.text
            await wait_for(client, f"http://order-service/order/{response.json()['id']}",
                           lambda body: body["status"] == "ReadyForDelivery")
            deliveries = (await client.get("http://delivery/deliveries/delivery")).json()
            assert len(deliveries) == 3
            assert deliveries[0]["address"] == "Legacy address A"
            assert deliveries[1]["address"] == "Legacy address B"
    run_scenario(scenario)
