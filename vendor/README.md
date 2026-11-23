# Chasis compartido

`microservice_chassis-0.6.1-py3-none-any.whl` es la distribución de
`chasis_homework` utilizada por los siete servicios Python. No requiere un
checkout hermano ni descargar una rama mutable para arrancar con Docker.

SHA-256: `8b3d9bfc44fc46c6f4a1d1b22cb11e0c1015dea3daf976ddc113558c356babc6`

La versión 0.6.1 añade dos módulos reutilizables:

- `my_chassis.discovery`: registro, baja y descubrimiento de servicios con Consul.
- `my_chassis.observability`: logs estructurados por RabbitMQ, middleware de
  disponibilidad y comprobaciones compartidas para operaciones de eventos.

`service_logging` conserva únicamente la configuración propia de este proyecto:
el exchange `manufacturing.logs` y las rutas de salud que ya existían.

La wheel 0.5.0 anterior se conserva; los Dockerfile y requisitos utilizan 0.6.1.
El monitor actualiza la salud aproximadamente cada segundo. Las llamadas normales
usan ese estado guardado; no consultan de nuevo Consul ni el health check del
colector. Si falla o tiene más de cuatro segundos, se bloquea la operación.
El envío confirmado de logs se conserva y no renueva la fecha del health check.

Las pruebas de esta versión no se han ejecutado; quedan para ejecución manual.
`test_log_aggregation.py` incluye los casos de lectura de caché y caducidad.

Para reconstruir desde el repositorio independiente:

```powershell
python tools/build_chassis.py --source ../chasis_homework
```

Para aplicar y comprobar manualmente desde la raíz del proyecto:

```powershell
docker compose up -d --build
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_log_aggregation.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_saga_history.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_order_access.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_tls.py
```

Si cambia la versión, actualizar los pins de requisitos y las rutas de la wheel
en todos los Dockerfile antes de reconstruir las imágenes.
