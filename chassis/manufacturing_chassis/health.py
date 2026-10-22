"""Endpoint sencillo de disponibilidad HTTP."""

from fastapi import APIRouter


def create_health_router(service_name: str) -> APIRouter:
    """Crea GET /health; no comprueba la base de datos ni RabbitMQ."""
    router = APIRouter(tags=["Health"])

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": service_name}

    return router
