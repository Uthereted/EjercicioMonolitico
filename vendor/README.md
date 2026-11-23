# Chasis compartido

`microservice_chassis-0.10.0-py3-none-any.whl` es la distribución de
`chasis_homework` utilizada por los siete servicios Python. No requiere un
checkout hermano ni descargar una rama mutable para arrancar con Docker.

SHA-256: `46348429da984b18c0e98652af58ae132d9d934ccac3a84e450fd9dea3136c7c`

El chasis incluye los siguientes módulos reutilizables:

- `my_chassis.messaging.ExchangeRouter`: separa hechos y mensajes de Saga y
  utiliza el exchange de respuesta indicado en el comando persistido.
- `my_chassis.health.ResourceMonitor`: CPU, memoria y disco con muestra periódica
  guardada en memoria; el extra `resources` instala psutil.
- `my_chassis.health.HealthMonitor`: comprobaciones periódicas de dependencias
  y respuestas de salud basadas solo en el estado guardado.
- `my_chassis.discovery`: registro, baja, descubrimiento y consultas GET con Consul.
  `query_service` conserva el JWT original y comprueba HTTPS/SNI. El extra
  `discovery` instala HTTPX para los consumidores de consultas.
- `my_chassis.observability`: logs estructurados por RabbitMQ, middleware de
  disponibilidad y comprobaciones compartidas para operaciones de eventos.

`service_logging` conserva únicamente la configuración propia de este proyecto:
el exchange `manufacturing.logs` y las rutas de salud que ya existían.

La wheel 0.5.0 anterior se conserva; los Dockerfile y requisitos utilizan 0.10.0.
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
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_consul_queries.py
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python order-service tests/test_domain_events.py --service order
docker compose run --rm --no-deps -v "${PWD}:/verification:ro" -w /verification --entrypoint python paymentservice tests/test_domain_events.py --service payment
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

## CQRS con Consul (0.10.0)

Las lecturas están separadas en `client_queries.py`, `order_queries.py` y
`payment_queries.py`. Devuelven los modelos de lectura con las mismas rutas y
permisos; consultar saldo no crea cuentas. Las escrituras mantienen sus comandos,
transacciones, Saga y eventos.

Pedidos obtiene la ficha y dirección actual del cliente mediante
`query_service('client-service', '/clients/{id}', access_token=...)`. Pagos usa la
misma consulta para validar al cliente antes de depositar. El chasis consulta
Consul y utiliza directamente la IP y puerto devueltos, con HTTPS y el JWT del
usuario. Las operaciones de la Saga siguen por RabbitMQ.

La consulta 404 indica un cliente inexistente; si el servicio no está disponible,
se devuelve 503 sin guardar el pedido ni efectuar el depósito. Los monitores de
pedidos y pagos también comprueban en segundo plano la disponibilidad registrada
del servicio de consultas de clientes. No se han ejecutado las pruebas.

## Recursos del health check (0.10.0)

Los siete servicios Python devuelven `resources` en `/service/health`:

- `sampled_at`: instante UTC en que se obtuvo la muestra.
- `cpu_percent`: porcentaje de CPU del sistema visible para el proceso.
- `memory`: memoria total, disponible y porcentaje usado.
- `disk`: ruta, capacidad, espacio libre y porcentaje usado.

Se recoge una muestra al arrancar y después cada cinco segundos en un hilo
auxiliar. Las llamadas HTTP solo devuelven el resultado guardado. Si no hay
muestra válida o tiene quince segundos, el health check devuelve 503. Las
comprobaciones de dependencias mantienen su propia caducidad de cuatro segundos.
`HEALTH_DISK_PATH` permite cambiar la ruta del disco, que por defecto es `/`.

En Docker, CPU y memoria pueden describir la máquina Linux anfitriona; no se
presentan como uso exclusivo ni como límites del contenedor. El gateway conserva
su comprobación de disponibilidad de backends; las métricas están en los servicios.

Tras reconstruir, comprueba los campos desde PowerShell (con la CA local confiada):

```powershell
Invoke-RestMethod https://localhost:13001/service/health | ConvertTo-Json -Depth 5
```

Los casos de caché y caducidad de recursos están en `tests/test_health_checks.py`.
`tests/test_tls.py` comprueba también su presencia en los siete servicios.
Las pruebas no se han ejecutado.

## Respuesta de Saga indicada en el comando (0.10.0)

Pedidos incluye `response_exchange: "manufacturing.saga.responses"` en cada
comando de reserva, creación, confirmación y compensación. Pagos y entregas
guardan ese dato en sus respuestas del outbox. `ExchangeRouter`, compartido
en el chasis, lo utiliza para elegir el exchange de publicación entre los
destinos permitidos. Los hechos siguen en `manufacturing.events` y los comandos
en `manufacturing.commands`. No se modifica la base de datos ni los identificadores.

Solo para mensajes ya pendientes sin el campo se conserva el destino anterior.
No se han ejecutado pruebas ni arrancado contenedores. Para aplicar los cambios:

```powershell
docker compose up -d --build
```

Al crear un pedido, los comandos archivados por Event Logger deben incluir
`data.response_exchange` y las respuestas conservar el mismo valor. Comprueba
tanto un pedido aceptado como uno rechazado que active la compensación.
`tests/test_saga_routing.py` contiene casos del enrutador para ejecución manual.
