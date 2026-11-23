import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import socket
from urllib.request import Request, urlopen
from urllib.parse import quote


def _get(url):
    with urlopen(url, timeout=2) as response:
        return json.load(response)


async def get_service_url(name):
    consul_url = os.environ["CONSUL_URL"].rstrip("/")
    instances = await asyncio.to_thread(
        _get, f"{consul_url}/v1/health/service/{quote(name, safe='')}?passing=true"
    )
    if not instances:
        raise RuntimeError(f"No healthy instances of {name} in Consul")
    instance = instances[0]
    service = instance["Service"]
    address = service["Address"] or instance["Node"]["Address"]
    scheme = service.get("Meta", {}).get("scheme", "https")
    return f"{scheme}://{address}:{service['Port']}"


def _put(url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    request = Request(url, data=body, method="PUT",
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        response.read()


@asynccontextmanager
async def registered_service(name):
    consul_url = os.getenv("CONSUL_URL")
    if not consul_url:
        yield
        return

    consul_url = consul_url.rstrip("/")
    hostname = socket.gethostname()
    address = socket.gethostbyname(hostname)
    service_id = f"{name}-{hostname}"
    scheme = os.getenv("API_SCHEME", "https")
    await asyncio.to_thread(_put, f"{consul_url}/v1/agent/service/register", {
        "ID": service_id,
        "Name": name,
        "Address": address,
        "Port": 8000,
        "Meta": {"scheme": scheme},
        "Check": {
            "HTTP": f"{scheme}://{address}:8000/service/health",
            "TLSServerName": name,
            "Interval": "10s",
            "Timeout": "2s",
        },
    })
    try:
        yield
    finally:
        try:
            await asyncio.to_thread(
                _put, f"{consul_url}/v1/agent/service/deregister/{service_id}"
            )
        except OSError:
            logging.getLogger(__name__).warning(
                "Could not deregister %s from Consul", service_id
            )
