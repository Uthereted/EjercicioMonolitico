# Chasis compartido

`microservice_chassis-0.7.0-py3-none-any.whl` es la distribución de
`chasis_homework` utilizada por los siete servicios Python. No requiere un
checkout hermano ni descargar una rama mutable para arrancar con Docker.

SHA-256: `3a38022e73bff937c3d9ecda69317968914cd2c6fef9c7bf6dfaecbc7e64357d`

El chasis incluye los siguientes módulos reutilizables:

- `my_chassis.health.HealthMonitor`: comprobaciones periódicas de dependencias
  y respuestas de salud basadas solo en el estado guardado.
- `my_chassis.discovery`: registro, baja y descubrimiento de servicios con Consul.
- `my_chassis.observability`: logs estructurados por RabbitMQ, middleware de
  disponibilidad y comprobaciones compartidas para operaciones de eventos.

`service_logging` conserva únicamente la configuración propia de este proyecto:
el exchange `manufacturing.logs` y las rutas de salud que ya existían.

La wheel 0.5.0 anterior se conserva; los Dockerfile y requisitos utilizan 0.7.0.
El monitor actualiza la salud aproximadamente cada segundo. Las llamadas normales
usan ese estado guardado; no consultan de nuevo Consul ni el health check del
colector. Si falla o tiene más de cuatro segundos, se bloquea la operación.
El envío confirmado de logs se conserva y no renueva la fecha del health check.

Las pruebas de esta versión no se han ejecutado; quedan para ejecución manual.
`test_log_aggregation.py` incluye los casos de caché y caducidad de logs.
`test_health_checks.py` cubre dependencias fallidas, clave pendiente, caducidad y
recuperación; está preparado para que lo ejecutes manualmente.

Para reconstruir desde el repositorio independiente:

```powershell
python tools/build_chassis.py --source ../chasis_homework
```

Para aplicar y comprobar manualmente desde la raíz del proyecto:

```powershell
docker compose up -d --build
# Espera a que los endpoints de salud indiquen disponibilidad antes de las pruebas TLS.
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_health_checks.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_log_aggregation.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_saga_history.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_order_access.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_tls.py
```

Si cambia la versión, actualizar los pins de requisitos y las rutas de la wheel
en todos los Dockerfile antes de reconstruir las imágenes.

## Qué comprueba cada servicio antes de devolver ok

- APIs de negocio: SQL, conexiones del publicador y consumidores RabbitMQ, logs,
  clave pública disponible y tareas activas. Pedidos también comprueba el MongoDB
  del historial de Saga; clientes valida la clave que genera localmente.
- Auditoría: MongoDB, conexión y canal de su consumidor, logs y tarea activa.
- Agregador: RabbitMQ y MongoDB (lectura/escritura), con resultado guardado y caducidad.
  Su salud representa la recepción de logs: exigir la clave del cliente aquí
  impediría que clientes publicase esa primera clave al arrancar con datos vacíos.
  La consulta de logs sigue validando JWT y su clave pública.
- Gateway: usa el estado guardado de sus siete backends; devuelve 503 si alguno
  no tiene instancias disponibles.

Los monitores son internos; el endpoint no ejecuta consultas SQL, pings MongoDB
ni solicitudes HTTP. No se han ejecutado las pruebas de esta versión.
