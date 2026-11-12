# Agregación de logs

Implementación basada en las páginas 7 y 14 de `06 - Reliability and Observability (Nov2024).pdf`.
Usa Python `logging`, un exchange topic nuevo, un consumidor propio, MongoDB y una API REST.

## Separación de mensajes

| Contenido | Exchange | Cola / consumidor | Colección MongoDB |
| --- | --- | --- | --- |
| Eventos de negocio | `manufacturing.events` | Colas de dominio y `audit.domain` / Event Logger | `business_events` para la auditoría |
| Comandos de saga | `manufacturing.commands` | Colas de saga y auditoría | `business_events` para la auditoría |
| Respuestas de saga | `manufacturing.saga.responses` | Colas de saga y auditoría | `business_events` para la auditoría |
| Mensajes internos de aplicaciones | **`manufacturing.logs` (nuevo)** | **`application.logs` / `log-aggregator` (nuevo)** | **`application_logs` (nueva)** |

Se comparte el contenedor MongoDB y la base `manufacturing_audit`, con colecciones separadas.
El Event Logger sigue registrando eventos. El agregador registra mensajes de aplicación.
Los logs nunca se publican en los exchanges de eventos, comandos o respuestas.

El agregador recibe además la clave pública de autenticación en la cola independiente
`logs.auth`, mediante el evento ya existente `auth.signing-key.published`.
La guarda en `log_auth_keys`; esa suscripción no transporta logs. Así verifica JWT
localmente, utilizando el mismo verificador del chasis y sin consultar al servicio
de autenticación por cada petición. Su disponibilidad no depende de haber recibido
la clave, para permitir el arranque del servicio de autenticación.

## Emisores y formato

Los cinco servicios de negocio y el Event Logger usan el paquete compartido local
`service_logging`, copiado en sus imágenes Docker. Se sustituye el destino de los
mensajes de Python `logging` por RabbitMQ. No hay almacenamiento alternativo en
consola ni en ficheros.

| Emisor | Ejemplos de routing keys |
| --- | --- |
| Order | `orders.debug`, `orders.error` |
| Payment | `payments.info`, `payments.critical` |
| Client/Auth | `clients.info`, `clients.warning` |
| Machine | `machine.info`, `machine.error` |
| Delivery | `deliveries.info`, `deliveries.error` |
| Event Logger | `event-logger.info`, `event-logger.error` |

Todos admiten `DEBUG`, `INFO`, `WARNING`, `ERROR` y `CRITICAL`. La clave tiene
la forma `<servicio>.<nivel-en-minúsculas>`. Los registros incluyen:

```json
{
  "log_id": "identificador-uuid",
  "timestamp": "2026-11-12T12:00:00+00:00",
  "service": "orders",
  "level": "ERROR",
  "message": "No se pudo procesar el pedido",
  "logger": "app.workflow",
  "file": "/home/pyuser/code/app/workflow.py",
  "line": 42,
  "exception": "Traceback si se ha utilizado logger.exception(...)"
}
```

El agregador añade `routing_key` y `received_at`. El identificador hace que una
reentrega del mismo mensaje no duplique el registro. Los mensajes son persistentes,
el exchange y la cola son duraderos, los productores esperan confirmación del broker
y el consumidor hace ACK después de una escritura confirmada y journaled en MongoDB.

El middleware registra método, ruta y estado HTTP. No incluye cabeceras, tokens,
contraseñas, parámetros de consulta ni cuerpos. También captura los mensajes de
aplicación, advertencias y excepciones existentes. Los diagnósticos del transporte
de logs se excluyen para evitar que enviar un log genere otro indefinidamente.

## Consulta REST

- Gateway: `GET http://localhost:8080/api/logs`.
- Acceso local directo: `GET http://localhost:13007/logs`.
- Swagger local: `http://localhost:13007/docs`.
- Se exige un JWT válido con rol `admin`: sin token devuelve 401; con un token
  válido sin ese rol devuelve 403. Si aún no se ha recibido la clave, devuelve 503.

| Parámetro | Ejemplo | Descripción |
| --- | --- | --- |
| `service` | `orders` | Uno de los seis nombres de emisor de la tabla |
| `level` | `ERROR` | Nivel exacto en mayúsculas |
| `date_from` | `2026-11-12T00:00:00Z` | Fecha inicial inclusiva, con zona horaria |
| `date_to` | `2026-11-13T00:00:00Z` | Fecha final inclusiva, con zona horaria |
| `limit` | `50` | Tamaño de página, entre 1 y 200; por defecto 50 |
| `offset` | `0` | Registros que se omiten; por defecto 0 |

Respuesta: `{"items": [...], "total": 123, "limit": 50, "offset": 0}`.
Se ordena de más reciente a más antiguo, con el ID como desempate.

## Comportamiento ante caídas

Según la decisión del proyecto, la aplicación deja de admitir operaciones si no
puede registrar actividad. No se cierran los procesos.

1. El agregador comprueba en segundo plano, cada segundo, su conexión RabbitMQ y
   la lectura/escritura en MongoDB. `/service/health` lee ese estado y devuelve 200 o 503.
2. Los seis emisores consultan ese endpoint interno antes de admitir una petición
   y antes de comenzar las operaciones de fondo. También comprueban su conexión
   al exchange y envían los logs pendientes con confirmación del broker.
3. Si falla esa comprobación, las peticiones HTTP devuelven 503. Los consumidores
   de negocio, la publicación de eventos, la publicación de claves y la fabricación
   esperan y reintentan cada segundo. La máquina comprueba disponibilidad antes
   de iniciar y antes de completar una pieza.
4. Las comprobaciones de salud de los emisores también pasan a 503; HAProxy los
   marca DOWN con el circuit breaker existente y devuelve 503 en sus rutas.
5. Al recuperarse la infraestructura, el trabajo se reanuda automáticamente y
   HAProxy vuelve a marcar los servicios UP.

La parada se produce en los puntos de comprobación: una operación que ya estuviera
en marcha puede terminar antes de detectar la caída. No se revierten transacciones
ya confirmadas. La detección de MongoDB/RabbitMQ depende del sondeo y sus tiempos
de espera; HAProxy añade los intervalos del circuit breaker. Los logs aún no
confirmados por RabbitMQ se conservan en memoria y se reintentan; reiniciar o matar
ese proceso puede perderlos. Los logs ya confirmados permanecen en la cola durable.

## Comprobación manual (PowerShell)

Estos pasos se proporcionan para ejecutarlos manualmente. No se han ejecutado tests
ni arrancado contenedores como parte de esta implementación.

### 1. Arrancar y comprobar el exchange nuevo

Desde la raíz del repositorio, con Docker Desktop abierto y `.env` preparado:

```powershell
docker compose up -d --build
docker compose ps
Invoke-RestMethod http://localhost:13007/service/health
```

Debe haber diez contenedores. Espera a que la salud del agregador devuelva `ok` y
los siete backends estén UP en `http://localhost:8404` (`admin` / `admin`).
En RabbitMQ (`http://localhost:15672`, `manufacturing` / `manufacturing`), comprueba
el exchange **`manufacturing.logs`**, la cola **`application.logs`** y sus seis bindings.
El consumidor declara la topología nueva incluso si el volumen RabbitMQ ya existía;
no hace falta borrar volúmenes ni datos para aplicar este cambio.

### 2. Autenticarse y consultar logs

```powershell
$basicAuth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('admin:admin123'))
$tokens = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/auth/token -Headers @{Authorization = "Basic $basicAuth"}
$adminHeaders = @{Authorization = "Bearer $($tokens.access_token)"}
Invoke-RestMethod http://localhost:8080/api/clients -Headers $adminHeaders
$result = Invoke-RestMethod 'http://localhost:8080/api/logs?service=clients&level=INFO&limit=10&offset=0' -Headers $adminHeaders
$result.items | Format-List
```

Debe aparecer actividad del servicio `clients`. Para ver todos los emisores:

```powershell
Invoke-RestMethod 'http://localhost:8080/api/logs?limit=100' -Headers $adminHeaders
```

Para fechas, usa un intervalo que incluya los registros de tu ejecución:

```powershell
Invoke-RestMethod 'http://localhost:8080/api/logs?date_from=2026-11-12T00%3A00%3A00Z&date_to=2026-11-13T00%3A00%3A00Z&limit=10' -Headers $adminHeaders
```

Sin token debe devolver 401:

```powershell
Invoke-WebRequest http://localhost:8080/api/logs
```

Repite la consulta con un token de un usuario normal registrado mediante
`POST /api/auth/register`: debe devolver 403. Prueba `limit=201`, un nivel inexistente
o una fecha sin zona horaria: deben devolver 422.

### 3. Generar expresamente los cinco niveles

Este comando abre un productor temporal dentro del contenedor de pedidos. Usa
el mismo handler que la aplicación y publica en el exchange de logs:

```powershell
@'
import asyncio
import logging
import app.main
from service_logging import runtime

async def main():
    async with runtime.lifespan():
        await runtime.checkpoint()
        logger = logging.getLogger('manual')
        for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
            logger.log(level, 'Comprobacion manual del nivel %s', logging.getLevelName(level))
        try:
            raise ValueError('Excepcion de ejemplo para comprobar el traceback')
        except ValueError:
            logger.exception('Comprobacion manual de excepciones')
        await runtime.checkpoint()

asyncio.run(main())
'@ | docker compose exec -T order-service python -

Invoke-RestMethod 'http://localhost:8080/api/logs?service=orders&limit=30' -Headers $adminHeaders
```

Espera a que el consumidor guarde los mensajes. Deben aparecer los cinco niveles
y un ERROR con el detalle de la excepción.

### 4. Verificar la separación en MongoDB

```powershell
docker compose exec mongodb mongosh
```

Dentro de MongoDB:

```javascript
use manufacturing_audit
db.application_logs.find().sort({timestamp:-1}).limit(5)
db.business_events.find().sort({received_at:-1}).limit(5)
```

La primera colección contiene mensajes, niveles y excepciones; la segunda contiene
eventos de negocio y sus datos.

### 5. Comprobar caída y recuperación

Conserva el token anterior. Para cada dependencia, detenla, comprueba el rechazo
y arráncala de nuevo antes de pasar a la siguiente:

```powershell
docker compose stop log-aggregator
Invoke-WebRequest http://localhost:13005/clients -Headers $adminHeaders
Invoke-WebRequest http://localhost:13005/service/health
docker compose start log-aggregator
```

Ambas consultas deben devolver 503, tras detectar la caída. Comprueba también
`http://localhost:8080/api/clients` y los estados DOWN en HAProxy. Su `/health`
propio permanece en 200. Al recuperar el agregador, espera a los estados UP y
repite la consulta autenticada: debe volver a funcionar sin reiniciar los emisores.

Repite el procedimiento con cada una de estas parejas de comandos:

```powershell
docker compose stop mongodb
# Comprobar 503 y pausa del trabajo.
docker compose start mongodb

docker compose stop rabbitmq
# Comprobar 503 y pausa del trabajo.
docker compose start rabbitmq
```

Con un pedido en fabricación, verifica que no se completa mientras la infraestructura
de logs está caída y que continúa al recuperarla. Consulta los logs desde la API
después de la recuperación; no se utiliza la consola como destino alternativo.

### Tests unitarios opcionales (creados, sin ejecutar)

Se han añadido casos para confirmar el ACK posterior al guardado, la reentrega
cuando falla MongoDB, el rechazo de eventos como logs, el acceso de administrador,
el bloqueo ante caída del agregador y la conservación de logs pendientes al fallar
el envío. Los tests de dominio, auditoría y refresh existentes aíslan ahora esta
dependencia de infraestructura mediante mocks.

Si quieres ejecutarlos en el entorno Python local con las dependencias instaladas:

```powershell
.\.venv\Scripts\python.exe tests/test_log_aggregation.py
```

Este archivo necesita las dependencias de `services/event_logger/requirements.txt`
y la wheel local del chasis, igual que el Event Logger.
