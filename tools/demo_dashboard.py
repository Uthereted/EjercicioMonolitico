"""Minimal presentation dashboard for the Manufacturing Microservices stack.

Run inside Docker (uses order-service's packages and mounted /tls/ca.crt):
  docker compose run --rm --no-deps -p 8888:8888 -v "${PWD}:/demo:ro" -w /demo --entrypoint python order-service tools/demo_dashboard.py
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib
import importlib.metadata
import inspect
import os
from pathlib import Path
import pkgutil
import socket
import ssl
import time
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import httpx
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = Path(__file__).resolve().with_name("demo_dashboard.html")

CA_FILE = os.getenv("TLS_CA_FILE") or str(ROOT / "infrastructure/tls/generated/ca.crt")
IN_DOCKER = Path("/.dockerenv").exists() or Path("/tls/ca.crt").exists()

GATEWAY_URL = os.getenv(
    "DEMO_GATEWAY_URL",
    "https://haproxy:8080" if IN_DOCKER else "https://localhost:8080",
).rstrip("/")
CONSUL_URL = os.getenv(
    "CONSUL_URL",
    "https://consul:8501" if IN_DOCKER else "https://localhost:8500",
).rstrip("/")
RABBITMQ_MGMT_URL = os.getenv(
    "DEMO_RABBITMQ_MGMT_URL",
    "https://rabbitmq:15671" if IN_DOCKER else "https://localhost:15672",
).rstrip("/")
MONGODB_URL = os.getenv(
    "MONGODB_URL",
    f"mongodb://mongodb:27017/?tls=true&tlsCAFile={CA_FILE}",
)
AUDIT_DB = os.getenv("AUDIT_DATABASE", "manufacturing_audit")

ADMIN_USER = os.getenv("TEST_AUTH_USERNAME", "admin")
ADMIN_PASS = os.getenv("TEST_AUTH_PASSWORD", "admin123")
RABBIT_USER = os.getenv("RABBITMQ_USER", "manufacturing")
RABBIT_PASS = os.getenv("RABBITMQ_PASS", "manufacturing")

SERVICES = [
    ("order-service", "order-service", 13001),
    ("client-service", "client_service", 13005),
    ("payment-service", "paymentservice", 13006),
    ("delivery-service", "delivery", 13004),
    ("machine-service", "machineservice", 13002),
    ("event-logger", "event-logger", None),
    ("log-aggregator", "log-aggregator", 13007),
]

app = FastAPI(title="Manufacturing Microservices Demo")

_token: dict[str, object] = {"value": None, "at": 0.0}
_token_lock = asyncio.Lock()


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=CA_FILE) if Path(CA_FILE).exists() else ssl.create_default_context()


def http_client(timeout: float = 4.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(verify=ssl_context(), trust_env=False, timeout=timeout)


def mongo_client(timeout_ms: int = 1500) -> MongoClient:
    return MongoClient(
        MONGODB_URL,
        serverSelectionTimeoutMS=timeout_ms,
        socketTimeoutMS=timeout_ms,
        connectTimeoutMS=timeout_ms,
        tz_aware=True,
    )


async def admin_token(force: bool = False) -> str:
    async with _token_lock:
        if _token["value"] and not force and time.monotonic() - float(_token["at"]) < 1800:
            return str(_token["value"])
        async with http_client(5.0) as client:
            r = await client.post(f"{GATEWAY_URL}/api/auth/token", auth=(ADMIN_USER, ADMIN_PASS))
            if r.status_code != 200:
                raise HTTPException(503, f"Admin login failed ({r.status_code})")
            _token["value"] = r.json()["access_token"]
            _token["at"] = time.monotonic()
            return str(_token["value"])


async def gw(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None, auth: bool = True) -> httpx.Response:
    headers = {"Authorization": f"Bearer {await admin_token()}"} if auth else {}
    async with http_client(8.0) as client:
        r = await client.request(method, f"{GATEWAY_URL}{path}", headers=headers, json=json_body, params=params)
        if auth and r.status_code == 401:
            headers["Authorization"] = f"Bearer {await admin_token(force=True)}"
            r = await client.request(method, f"{GATEWAY_URL}{path}", headers=headers, json=json_body, params=params)
        return r


async def fetch_consul(client: httpx.AsyncClient) -> tuple[dict, dict]:
    try:
        sr, cr = await asyncio.gather(
            client.get(f"{CONSUL_URL}/v1/agent/services"),
            client.get(f"{CONSUL_URL}/v1/agent/checks"),
        )
        sr.raise_for_status()
        cr.raise_for_status()
        return sr.json(), cr.json()
    except Exception:
        return {}, {}


async def probe_health(client: httpx.AsyncClient, name: str, docker_host: str, local_port: int | None, instances: list[dict]) -> dict:
    if instances:
        inst = instances[0]
        scheme = inst.get("Meta", {}).get("scheme", "https")
        addr = f"{inst['Address']}:{inst['Port']}"
        url, sni = f"{scheme}://{addr}/service/health", name
    elif IN_DOCKER:
        addr = f"{docker_host}:8000"
        url, sni = f"https://{addr}/service/health", docker_host
    elif local_port:
        addr = f"localhost:{local_port}"
        url, sni = f"https://{addr}/service/health", "localhost"
    else:
        return {"service": name, "ok": False, "status_code": 0, "address": "internal", "resources": None}

    try:
        r = await client.get(url, extensions={"sni_hostname": sni})
        body = r.json() if r.content else {}
        return {
            "service": name,
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "address": addr,
            "resources": body.get("resources") if isinstance(body, dict) else None,
        }
    except Exception:
        return {"service": name, "ok": False, "status_code": 0, "address": addr, "resources": None}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(HTML_PATH.read_text(encoding="utf-8"))


@app.get("/demo/status")
async def demo_status():
    async with http_client(3.0) as client:
        try:
            gr = await client.get(f"{GATEWAY_URL}/health")
            gateway = {"ok": gr.status_code == 200, "status_code": gr.status_code}
        except Exception:
            gateway = {"ok": False, "status_code": 0}

        svc_map, chk_map = await fetch_consul(client)
        by_name: dict[str, list[dict]] = {}
        for s in svc_map.values():
            by_name.setdefault(s.get("Service", ""), []).append(s)

        services = await asyncio.gather(*[
            probe_health(client, n, h, p, by_name.get(n, [])) for n, h, p in SERVICES
        ])

        checks_by_sid = {c.get("ServiceID"): c for c in chk_map.values() if c.get("ServiceID")}
        consul = [
            {
                "name": s.get("Service"),
                "id": sid,
                "address": f"{s.get('Address')}:{s.get('Port')}",
                "scheme": s.get("Meta", {}).get("scheme", "https"),
                "status": checks_by_sid.get(sid, {}).get("Status", "unknown"),
            }
            for sid, s in sorted(svc_map.items(), key=lambda kv: kv[1].get("Service", ""))
        ]

        return {"gateway": gateway, "services": services, "consul": consul}


@app.get("/demo/health-cache")
async def demo_health_cache():
    async with http_client(3.0) as client:
        svc_map, _ = await fetch_consul(client)
        inst = next((s for s in svc_map.values() if s.get("Service") == "order-service"), None)
        url = (
            f"https://{inst['Address']}:{inst['Port']}/service/health"
            if inst
            else ("https://order-service:8000/service/health" if IN_DOCKER else "https://localhost:13001/service/health")
        )
        sni = "order-service" if (inst or IN_DOCKER) else "localhost"
        calls = []
        for i in range(2):
            if i == 1:
                await asyncio.sleep(0.6)
            t0 = time.monotonic()
            r = await client.get(url, extensions={"sni_hostname": sni})
            data = r.json() if r.content else {}
            calls.append({
                "status": r.status_code,
                "ms": round((time.monotonic() - t0) * 1000, 1),
                "sampled_at": (data.get("resources") or {}).get("sampled_at"),
            })
        return {"same_snapshot": calls[0]["sampled_at"] is not None and calls[0]["sampled_at"] == calls[1]["sampled_at"], "calls": calls}


class ScenarioIn(BaseModel):
    scenario: str = Field(pattern="^(happy|insufficient_funds|outside_coverage)$")


@app.post("/demo/saga/run")
async def demo_saga_run(body: ScenarioIn):
    tag = uuid.uuid4().hex[:8]
    funds, zip_code = {
        "happy": (100, "01001"),
        "insufficient_funds": (0, "01001"),
        "outside_coverage": (100, "28001"),
    }[body.scenario]

    reg = await gw("POST", "/api/auth/register", auth=False, json_body={
        "username": f"demo-{tag}", "password": f"Demo!{tag}",
        "name": f"Demo {tag}", "email": f"demo-{tag}@example.org",
        "address": "Calle Mayor 10", "postal_code": zip_code,
    })
    if reg.status_code != 201:
        raise HTTPException(reg.status_code, reg.text)
    client_id = reg.json()["id"]

    if funds > 0:
        dep = await gw("POST", f"/api/payments/{client_id}/deposit", json_body={"amount": funds})
        if dep.status_code not in (200, 201):
            raise HTTPException(dep.status_code, dep.text)

    ord_r = await gw("POST", "/api/orders", json_body={
        "client_id": client_id, "number_of_pieces": 2, "description": f"Demo ({body.scenario})",
    })
    if ord_r.status_code != 202:
        raise HTTPException(ord_r.status_code, ord_r.text)
    return {"order_id": ord_r.json()["id"], "client_id": client_id}


@app.get("/demo/saga/order/{order_id}")
async def demo_saga_order(order_id: int):
    ord_r, hist_r = await asyncio.gather(
        gw("GET", f"/api/orders/{order_id}"),
        gw("GET", f"/api/orders/{order_id}/saga/history", params={"limit": 50}),
    )
    if ord_r.status_code != 200:
        raise HTTPException(ord_r.status_code, ord_r.text)
    order = ord_r.json()
    history = hist_r.json().get("items", []) if hist_r.status_code == 200 else []

    balance = None
    if order.get("client_id"):
        br = await gw("GET", f"/api/payments/{order['client_id']}/balance")
        if br.status_code == 200:
            balance = br.json().get("balance")

    delivery = None
    if order.get("delivery_id"):
        dr = await gw("GET", f"/api/deliveries/{order['delivery_id']}")
        if dr.status_code == 200:
            delivery = dr.json()

    def _saga_msgs():
        try:
            with mongo_client() as m:
                docs = m[AUDIT_DB]["business_events"].find(
                    {"data.order_id": order_id, "event_type": {"$regex": r"^saga\."}}
                ).sort("received_at", 1)
                return [
                    {
                        "event_type": d.get("event_type"),
                        "producer": d.get("producer"),
                        "response_exchange": (d.get("data") or {}).get("response_exchange"),
                    }
                    for d in docs
                ]
        except Exception:
            return []

    return {
        "order": order,
        "balance": balance,
        "delivery": delivery,
        "history": history,
        "saga_messages": await asyncio.to_thread(_saga_msgs),
    }


@app.post("/demo/deliver/{delivery_id}")
async def demo_deliver(delivery_id: int):
    r = await gw("PATCH", f"/api/deliveries/{delivery_id}/status", json_body={"status": "Delivered", "tracking_number": f"DEMO-{delivery_id}"})
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    return r.json()


@app.get("/demo/sagas/latest")
async def demo_sagas_latest():
    r = await gw("GET", "/api/orders/sagas/latest", params={"limit": 10})
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    return r.json()


@app.get("/demo/logs")
async def demo_logs(service: str | None = None, level: str | None = None, limit: int = Query(20, ge=1, le=100)):
    params: dict[str, object] = {"limit": limit}
    if service:
        params["service"] = service
    if level:
        params["level"] = level
    r = await gw("GET", "/api/logs", params=params)
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    return r.json()


@app.get("/demo/cqrs")
async def demo_cqrs():
    from my_chassis.discovery import get_service_url, query_service

    token = await admin_token()
    t0 = time.monotonic()
    url = await get_service_url("client-service")
    clients = await query_service("client-service", "/clients", access_token=token)
    return {
        "discovered_url": url,
        "sni": "client-service",
        "ms": round((time.monotonic() - t0) * 1000, 1),
        "count": len(clients) if isinstance(clients, list) else 0,
    }


@app.get("/demo/security")
async def demo_security():
    checks = []
    ctx = ssl_context()
    async with http_client(3.0) as client:
        # 1. CA-verified HTTPS endpoints
        for label, url, auth in (
            ("HAProxy HTTPS + CA", f"{GATEWAY_URL}/health", None),
            ("Consul HTTPS + CA", f"{CONSUL_URL}/v1/status/leader", None),
            ("RabbitMQ HTTPS + CA", f"{RABBITMQ_MGMT_URL}/api/connections", (RABBIT_USER, RABBIT_PASS)),
        ):
            try:
                r = await client.get(url, auth=auth)
                stream = r.extensions.get("network_stream")
                ssl_obj = stream.get_extra_info("ssl_object") if stream else None
                detail = f"HTTP {r.status_code} ({ssl_obj.version() if ssl_obj else 'no tls'})"
                ok = r.status_code == 200 and ssl_obj is not None
                if "RabbitMQ" in label and ok:
                    conns = r.json()
                    tls_conns = sum(1 for c in conns if c.get("ssl"))
                    ok = len(conns) > 0 and tls_conns == len(conns)
                    detail = f"{tls_conns}/{len(conns)} AMQP connections over TLS"
                checks.append({"name": label, "passed": ok, "detail": detail})
            except Exception as exc:
                checks.append({"name": label, "passed": False, "detail": str(exc)})

        # 2. Wrong hostname rejected
        def _wrong_sni():
            host = "haproxy" if IN_DOCKER else "localhost"
            try:
                with socket.create_connection((host, 8080), timeout=2) as sock:
                    try:
                        ctx.wrap_socket(sock, server_hostname="wrong-host.invalid")
                        return False, "Accepted wrong hostname"
                    except ssl.SSLCertVerificationError:
                        return True, "SSLCertVerificationError raised as expected"
            except Exception as exc:
                return False, str(exc)

        ok, detail = await asyncio.to_thread(_wrong_sni)
        checks.append({"name": "CA Hostname Verification", "passed": ok, "detail": detail})

        # 3. Plaintext ports closed
        def _plaintext():
            ports = [("rabbitmq", 5672), ("consul", 8500), ("consul", 8600)] if IN_DOCKER else [("localhost", 5672), ("localhost", 8600)]
            open_p = []
            for h, p in ports:
                try:
                    with socket.create_connection((h, p), timeout=1):
                        open_p.append(f"{h}:{p}")
                except OSError:
                    pass
            return not open_p, ("Closed: " + ", ".join(f"{h}:{p}" for h, p in ports)) if not open_p else f"Open: {open_p}"

        ok, detail = await asyncio.to_thread(_plaintext)
        checks.append({"name": "Plaintext Ports Disabled", "passed": ok, "detail": detail})

        # 4. MongoDB requireTLS
        def _mongo():
            try:
                with mongo_client(1500) as m:
                    tls_ok = m.admin.command("ping").get("ok") == 1
            except Exception as exc:
                return False, str(exc)
            try:
                with MongoClient(f"mongodb://{'mongodb' if IN_DOCKER else 'localhost'}:27017", serverSelectionTimeoutMS=700, connectTimeoutMS=400) as pm:
                    pm.admin.command("ping")
                    return False, "Plaintext MongoDB accepted"
            except (ServerSelectionTimeoutError, PyMongoError):
                return tls_ok, "TLS OK, plaintext rejected"

        ok, detail = await asyncio.to_thread(_mongo)
        checks.append({"name": "MongoDB requireTLS", "passed": ok, "detail": detail})

        # 5. JWT required
        try:
            r = await client.get(f"{GATEWAY_URL}/api/orders")
            checks.append({"name": "JWT Authentication", "passed": r.status_code == 401, "detail": f"Unauthenticated /api/orders -> HTTP {r.status_code}"})
        except Exception as exc:
            checks.append({"name": "JWT Authentication", "passed": False, "detail": str(exc)})

    return {"checks": checks}


@app.get("/demo/chassis")
async def demo_chassis():
    import my_chassis

    modules = []
    for mod_info in sorted(pkgutil.iter_modules(my_chassis.__path__), key=lambda m: m.name):
        mod = importlib.import_module(f"my_chassis.{mod_info.name}")
        doc = (inspect.getdoc(mod) or "").splitlines()[0] if inspect.getdoc(mod) else ""
        modules.append({"name": f"my_chassis.{mod_info.name}", "doc": doc})
    return {"version": importlib.metadata.version("microservice_chassis"), "modules": modules}


def main():
    port = int(os.getenv("DEMO_PORT", "8888"))
    print(f"Demo dashboard running on http://localhost:{port}", flush=True)
    try:
        from hypercorn.asyncio import serve
        from hypercorn.config import Config

        cfg = Config()
        cfg.bind = [f"0.0.0.0:{port}"]
        cfg.accesslog = None
        asyncio.run(serve(app, cfg))
    except ImportError:
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=port, access_log=False)


if __name__ == "__main__":
    main()
