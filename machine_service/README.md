# Machine Microservice

Microservicio mínimo con un único endpoint funcional: `GET /machine/status`.

## Estructura

```
app/
├── main.py              # arranque de FastAPI, crea la Machine al iniciar
├── async_machine.py     # tu simulador de máquina (sin modificar)
├── dependencies.py       # get_db, get_machine
├── database.py           # engine + sesión async (SQLite por defecto)
├── routers/
│   ├── machine_router.py # "/" y "/machine/status"
│   └── router_utils.py
└── sql/
    ├── models.py          # Piece, Order (SQLAlchemy)
    ├── crud.py            # funciones de acceso a BD que usa Machine
    └── schemas.py         # Message, MachineStatusResponse (Pydantic)
```

## Ejecutar

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Luego:
- Documentación interactiva: http://127.0.0.1:8000/docs
- Estado de la máquina: http://127.0.0.1:8000/machine/status

Al arrancar, `Machine.create()` mira en la base de datos SQLite
(`machine_service.db`, se crea sola) si hay piezas pendientes o en
fabricación y las vuelve a encolar. Como no hay endpoint para crear
pedidos/piezas en este microservicio, la cola empezará vacía y el
estado será `"Waiting"` — es normal.

## Ejecutar con Docker

El contenedor usa `hypercorn` (vía `entrypoint.sh`), no `uvicorn`.

```bash
docker build -t machine-service .
docker run --rm -p 8000:8000 -e SERVICE_NAME=machine-service machine-service
```

`SERVICE_NAME` es opcional (solo se usa para el log de arranque). El
`Dockerfile` instala como root, copia el código como el usuario no
root `pyuser` (uid 1000) y ejecuta el servicio con ese usuario.

## Notas
- La BD es SQLite async solo para poder arrancar el servicio de forma
  aislada. En producción, cambia `DATABASE_URL` en `app/database.py`
  por la BD real que comparte el resto del sistema (o mejor aún,
  inyéctala por variable de entorno).
- Si en el futuro quieres añadir endpoints para crear/eliminar piezas
  o pedidos, recupera esa parte de tu `main_router.py` original.
