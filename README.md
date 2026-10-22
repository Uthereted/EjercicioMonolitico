# Manufacturing Microservices

El proyecto está dividido en cinco microservicios FastAPI: pedidos, máquinas,
entregas, clientes y pagos. Cada servicio tiene su propio código, contenedor,
Dockerfile y `requirements.txt`.

## Estructura

```text
services/
├── client/       # Clientes
├── delivery/     # Entregas
├── machine/      # Máquinas y cola de fabricación
├── order/        # Pedidos y piezas
└── payment/      # Pagos
haproxy/          # Configuración del gateway
compose.yml       # Ejecución conjunta
dot_env_example   # Variables de entorno de ejemplo
```

Cada carpeta de servicio contiene su código en `app/`, su Dockerfile y su
`requirements.txt` con las dependencias que utiliza.
Los servicios usan `entrypoint.sh` y Hypercorn, salvo máquinas, que mantiene
`main.py` en su raíz y arranca con Uvicorn.

Pedidos incluye un Compose independiente en `services/order/compose.yml`,
su volumen en `services/order/db_volume/` y documentación en `services/order/docs/`.
Clientes utiliza `db_volume/`, entregas `delivery_volume/` y pagos
`payment_db_volume/`. Las bases se crean automáticamente al arrancar si no existen.

## Chasis compartido

Pedidos y pagos utilizan dos funciones del chasis: configuración común de logs
y un endpoint `GET /health` que identifica el servicio. Cada microservicio importa
las funciones que necesita; clientes, máquinas y entregas mantienen su código actual.

El chasis vive en un repositorio independiente:
[chasis_homework](https://github.com/Uthereted/chasis_homework).
Los requisitos de pedidos y pagos lo descargan directamente de GitHub. Solo
es necesario clonar este proyecto; no hace falta una carpeta local del chasis.

Pedidos y pagos importan `configure_logging` y `create_health_router` desde
`my_chassis`. No hace falta arrancar una aplicación de chasis: es una librería
instalada en cada servicio. El repositorio separado contiene las funciones,
la configuración de instalación y un README de uso.

Para comprobarlo después de reconstruir y arrancar los servicios:

```bash
curl http://localhost:13001/health
curl http://localhost:13006/health
```

El primer endpoint devuelve `{"status":"ok","service":"order-service"}` y el
segundo `{"status":"ok","service":"payment-service"}`. No comprueban la base
de datos ni RabbitMQ. El `/health` del gateway en el puerto 8080 es independiente.

La dependencia utiliza la rama `main` del chasis. Después de subir cambios al
chasis, ejecutar `docker compose build --no-cache order-service paymentservice`
y `docker compose up -d order-service paymentservice` para reinstalarlo.

## Dependencias e imágenes Docker

Cada microservicio instala las librerías de su propio `requirements.txt`.
Docker reutiliza la capa de instalación mientras no cambien ese archivo, la
imagen Python o las instrucciones anteriores del Dockerfile. Cambiar únicamente
el código no requiere reinstalar las dependencias. Evitar `--no-cache` y conservar
la caché de Docker permite aprovechar esta reutilización.

Pedidos y pagos instalan `microservice_chassis` desde GitHub para configurar
los logs y ofrecer `GET /health`. Python lo importa como `my_chassis`.
Los Dockerfile incluyen Git y certificados para descargarlo durante la construcción.
En otro PC, clonar este repositorio, copiar `dot_env_example` a `.env` y ejecutar
`docker compose up -d --build`. Fuera de Docker, instalar Git y ejecutar
`python -m pip install -r requirements.txt` desde la carpeta del servicio.
`email-validator` es necesario para los correos de clientes.
`SQLAlchemy[asyncio]` incluye `greenlet`, necesario para el acceso asíncrono
a las bases de datos.

Para construir todos los servicios:

```bash
docker compose build
```

Para reconstruir y arrancar solo pedidos después de cambiar su código:

```bash
docker compose up -d --build --no-deps order-service
```

Si se modifican las librerías de un servicio, reconstruir su imagen con
`docker compose up -d --build --no-deps <nombre-del-servicio>`.

## Ejecutar con Docker Compose

Desde la raíz del repositorio, copiar el archivo de ejemplo a `.env` si todavía
no existe. En PowerShell:

```powershell
if (!(Test-Path .env)) { Copy-Item dot_env_example .env }
docker compose up -d --build
```

Para validar la configuración, consultar los contenedores o detenerlos:

```bash
docker compose config
docker compose ps
docker compose down
```

Para ejecutar únicamente pedidos:

```bash
docker compose -f services/order/compose.yml up -d --build
```

## Acceso a los servicios

| Servicio | Nombre en Compose | Puerto local | Documentación API |
| --- | --- | --- | --- |
| Pedidos | `order-service` | 13001 | http://localhost:13001/docs |
| Máquinas | `machineservice` | 13002 | http://localhost:13002/docs |
| Entregas | `delivery` | 13004 | http://localhost:13004/docs |
| Clientes | `client_service` | 13005 | http://localhost:13005/client |
| Pagos | `paymentservice` | 13006 | http://localhost:13006/payment |

## API Gateway

### Autenticación tras el merge

Clientes conserva una única ruta `POST /clients` y añade `POST /auth/register`,
`GET /auth/token` (HTTP Basic) y `GET /auth/public-key`. En el gateway son
`/api/auth/register`, `/api/auth/token` y `/api/auth/public-key`.
El login devuelve un JWT RS256 válido durante una hora. Para las operaciones
protegidas, enviar `Authorization: Bearer <access_token>`: crear/listar pedidos
y consultar/ingresar/cobrar saldo. Las demás rutas mantienen su comportamiento
anterior; esta integración no añade autenticación a todas las APIs.

Para las pruebas locales se conserva el usuario inicial `admin` con contraseña
`admin123`. El registro público solo crea usuarios con rol `user`. Los usuarios
normales se asocian al cliente con su mismo email al obtener el token; los IDs
de las tablas de usuarios y clientes son independientes. Esto es autenticación
de desarrollo: el registro no verifica la propiedad del correo.

Clientes genera la clave de firma al arrancar y publica `client.cert.updated`.
Pedidos y pagos reciben ese aviso en colas exclusivas separadas de `order.events`
y actualizan la clave pública; también la recuperan al detectar una firma antigua.
Reiniciar clientes invalida los tokens anteriores: hay que iniciar sesión otra vez.
El consumidor dedicado de `order.events` sigue siendo el único que procesa
los eventos de piezas y entregas.

El gateway también escucha en `https://localhost:8443` con el certificado local
de `haproxy/gateway.pem`. Para pruebas HTTP se mantiene el puerto 8080.

Las pruebas `tests/test_auth_integration.py` y `tests/test_workflow_integration.py`
usan el gateway indicado por `API_BASE_URL` (por defecto `http://localhost:8080`).
La segunda obtiene primero un token; permite cambiar las credenciales con
`TEST_AUTH_USERNAME` y `TEST_AUTH_PASSWORD`. `tests/test_auth_verifier.py` se
ejecuta por separado en los entornos de pedidos y pagos.

HAProxy permite acceder a los cinco servicios mediante **http://localhost:8080**.
Los puertos individuales de la tabla se conservan para depuración. Las llamadas
internas entre servicios siguen usando la red Docker.

| Operación | Método | Ruta en el gateway |
| --- | --- | --- |
| Estado del gateway | GET | `/health` |
| Crear / listar clientes | POST / GET | `/api/clients` |
| Consultar cliente | GET | `/api/clients/{id}` |
| Ingresar saldo | POST | `/api/payments/{client_id}/deposit` |
| Consultar saldo | GET | `/api/payments/{client_id}/balance` |
| Crear / listar pedidos | POST / GET | `/api/orders` |
| Consultar pedido | GET | `/api/orders/{id}` |
| Consultar piezas del pedido | GET | `/api/orders/{id}/pieces` |
| Reintentar pedido pendiente | POST | `/api/orders/{id}/retry` |
| Consultar piezas | GET | `/api/pieces` |
| Consultar máquina | GET | `/api/machine/status` |
| Crear / listar entregas | POST / GET | `/api/deliveries` |
| Consultar entrega | GET | `/api/deliveries/{id}` |
| Actualizar entrega | PATCH | `/api/deliveries/{id}/status` |

Los cuerpos JSON y los estados son los mismos que en las APIs de los servicios.
Por ejemplo, una entrega se completa con `{"status":"Delivered"}`.
Las URLs anteriores `/api/order/order` y `/api/delivery/delivery` siguen disponibles.
Las rutas desconocidas devuelven 404. `/health` confirma que el gateway está activo;
la disponibilidad de cada servicio se comprueba con sus peticiones y con las
estadísticas de HAProxy en http://localhost:8404 (usuario y contraseña `admin`).
La documentación Swagger continúa en los puertos individuales de la tabla.

### Probar el flujo manualmente

Con Docker Desktop abierto, ejecutar desde la raíz del repositorio:

```powershell
docker compose up -d
docker compose restart haproxy
```

Si todavía no se han construido las imágenes, usar `docker compose up -d --build`
en el primer comando.

Desde Postman o Bruno, usar `http://localhost:8080` como URL base y
`Content-Type: application/json` en las peticiones con cuerpo. Guardar los
identificadores que devuelva cada respuesta:

1. **Crear cliente:** `POST /api/clients`.

   ```json
   {"name":"Cliente de prueba","email":"prueba@example.org"}
   ```

2. **Ingresar saldo:** `POST /api/payments/{client_id}/deposit`.

   ```json
   {"amount":100}
   ```

3. **Crear pedido:** `POST /api/orders`. Sustituir `client_id` por el del cliente.

   ```json
   {
     "client_id":1,
     "number_of_pieces":3,
     "description":"Prueba manual",
     "address":"Calle Mayor 10"
   }
   ```

4. **Consultar fabricación:** `GET /api/orders/{order_id}` y
   `GET /api/orders/{order_id}/pieces`. Esperar a que el pedido sea
   `ReadyForDelivery` y las tres piezas sean `Manufactured`.
   El pedido devuelve el `delivery_id`.
5. **Completar entrega:** `PATCH /api/deliveries/{delivery_id}/status`.

   ```json
   {"status":"Delivered","tracking_number":"PRUEBA-001"}
   ```

6. **Comprobar resultado:** `GET /api/orders/{order_id}` debe devolver
   `Delivered`, y `GET /api/payments/{client_id}/balance` debe devolver saldo **70**.

El precio es 10 unidades por pieza. Usar un email diferente al crear otro cliente.

Para consultar el gateway o diagnosticar un fallo:

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/api/orders
docker compose logs --tail=100 haproxy
```

## Desarrollo local

Crear un entorno virtual e instalar las dependencias del microservicio que se
quiera ejecutar. Usar su carpeta como directorio de trabajo, por ejemplo
`services/order`.

```bash
cd services/order
pip install -r requirements.txt
hypercorn app.main:app --bind 0.0.0.0:8000
```

Configurar `SQLALCHEMY_DATABASE_URL` cuando se necesite una base de datos distinta
de la local por defecto. El ejemplo de pedidos está en
`services/order/dot_env_example`.

Para máquinas, ejecutar desde `services/machine`:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Pruebas API y documentación histórica

La colección Bruno de pedidos está en `services/order/docs/bruno_collection/`.
El entorno `Docker Compose` utiliza el puerto `13001`.
Antes de crear un pedido, registrar el cliente, ingresar saldo y configurar
`clientId` en el entorno Bruno. El ejemplo de cinco piezas requiere 50 unidades.
La consulta de máquinas utiliza `machineURL`. El ejemplo de actualización a
`Finished` requiere que todas las piezas estén fabricadas; la eliminación de
un pedido del flujo devuelve 409.
El entorno Bruno `API Gateway` permite ejecutar la colección por el puerto `8080`.

La carpeta raíz `docs/` y los diagramas UML heredados conservan documentación
histórica de la aplicación original. Sus rutas y diagramas no representan
necesariamente la arquitectura actual; para probar pedidos, utilizar la
colección indicada arriba.

## RabbitMQ y evento order.created

Compose incluye RabbitMQ con almacenamiento persistente y panel en
http://localhost:15672 (usuario y contraseña de desarrollo: `manufacturing`).
El puerto AMQP es `5672`. Pedidos, pagos, máquinas y entregas usan `aio-pika`.

```text
order-service -> manufacturing.events (topic)
                  routing key: order.created
                          -> payment.events -> paymentservice

paymentservice -> manufacturing.events (topic)
                  routing keys: payment.approved / payment.rejected
                          -> order.payment.events -> order-service

delivery      -> manufacturing.events (topic)
                  routing key: delivery.status.update
                          -> order.events -> order-service

order-service -> manufacturing.events (topic)
                  routing key: machine.pieces.requested
                          -> machine.events -> machineservice

machineservice -> manufacturing.events (topic)
                  routing key: piece.status.update
                          -> order.events -> order-service
```

Hay un exchange de aplicación durable y cuatro colas durables: `payment.events`
para pagos, `order.payment.events` para resultados de pago y `order.events`
para avisos de entrega y piezas a pedidos, y `machine.events` para solicitudes
de fabricación. Un único consumidor de `order.events` distingue ambos tipos
por su routing key. La creación de entregas y el cambio a `Ready` mantienen
sus llamadas REST actuales. Clientes no consume estos eventos.

Tras aceptar el pago, pedidos publica `machine.pieces.requested` con
`{order_id, piece_ids}`. Guarda `manufacturing_requested=true` solo después
de la confirmación del broker; los fallos quedan pendientes para reintento.
Máquinas publica `piece.status.update` con `{piece_id, status}`, primero
`Manufacturing` y después `Manufactured`, reintentando hasta recibir confirmación.
Pedidos guarda cada cambio antes de confirmar el mensaje. Los duplicados no
hacen retroceder una pieza terminada. Máquinas evita encolar de nuevo piezas
ya completadas en el proceso actual, aunque la recuperación REST todavía las
devuelva mientras pedidos consume sus eventos. Tras reiniciar, recupera desde
pedidos las piezas pagadas que siguen pendientes.

Entregas publica `delivery.status.update` únicamente al llegar a `Delivered`,
con `{delivery_id, order_id, status}`. Pedidos consume `order.events`, comprueba
que la entrega pertenece al pedido y aplica las mismas validaciones de pago y
fabricación que la actualización REST. Confirma el mensaje después de guardar
el estado `Delivered`; los fallos de procesamiento se reencolan y los mensajes
inválidos se rechazan. Recibir el mismo aviso otra vez no cambia el resultado.
`Ready` y `Sent` no publican eventos.

El mensaje contiene `{order_id, client_id, amount}`. El propio pedido guarda
`order_created_published`, que solo se activa tras la confirmación del broker.
Así se reintenta la publicación si RabbitMQ falla, sin una tabla adicional.

El contenedor se configura en `compose.yml`. Los módulos `messaging.py` de
pedidos y pagos contienen las llamadas a RabbitMQ para publicar y consumir;
Compose por sí solo no ejecuta esa lógica de Python.

Pagos consume `payment.events`, realiza el cobro y publica `payment.approved`
o `payment.rejected`, con `{order_id, client_id, amount, accepted, reason}`.
Confirma `order.created` después de que el broker confirme la publicación del
resultado; si falla, lo reencola con una espera de cinco segundos. Pedidos consume
`order.payment.events` y confirma el resultado tras procesarlo. Los cobros y resultados
rechazados se conservan por pedido para que una redelivery no cambie el resultado
ni cobre dos veces. Los mensajes con formato o datos contradictorios se rechazan.
El resultado de pago ya no se comunica por REST. Pedidos conserva el endpoint
anterior por compatibilidad, con las mismas validaciones que el consumidor.

Para arrancar el proyecto actualizado:

```bash
docker compose up -d --build
docker compose logs -f order-service paymentservice rabbitmq
```

Crear un pedido devuelve **202** con `PendingPayment`. Consultar después
`GET /api/orders/{id}` para conocer el resultado; saldo insuficiente deja el pedido
en `Rejected`, con el motivo en `integration_error`. La validación de cliente
continúa siendo síncrona y devuelve 404 si no existe.

Para desarrollo fuera de Docker, usar las variables `RABBITMQ_URL` y
`RABBITMQ_EXCHANGE` del archivo de ejemplo. El Compose independiente de pedidos
usa el RabbitMQ del host y requiere pagos y el resto de servicios en ejecución.

Pruebas de persistencia, callbacks y cobros duplicados (con las dependencias
de pedidos instaladas):

```bash
python tests/test_order_messaging.py
python tests/test_payment_messaging.py
python tests/test_delivery_messaging.py
python tests/test_machine_messaging.py
```

Prueba completa con todos los servicios arrancados y `httpx` instalado:

```bash
python tests/test_workflow_integration.py
```

Crea un cliente de prueba, ingresa 100 y pide dos piezas; comprueba los estados
de fabricación, la entrega por evento y el saldo final de 80. Conserva esos
registros para poder revisarlos. `API_BASE_URL` permite cambiar el gateway
(por defecto `http://localhost:8080`).

## Flujo de pedidos

1. Crear el cliente con `POST /clients` en clientes.
2. Ingresar saldo con `POST /payment/{client_id}/deposit` en pagos.
3. Crear el pedido con `POST /order` en pedidos. El servicio valida al cliente,
   calcula el importe y publica `order.created` para que pagos cobre. El precio fijo es **10 unidades de
   saldo por pieza**; no se introduce en la petición.
4. Si el pago se acepta, el pedido solicita la fabricación de sus piezas en
   máquinas y crea la entrega con estado `Pending`.
5. Máquinas notifica el inicio y fin de cada pieza mediante el evento
   `piece.status.update`. Cuando todas terminan, pedidos marca la entrega
   como `Ready` y el pedido como `ReadyForDelivery`.
6. Actualizar la entrega con `PATCH /deliveries/delivery/{delivery_id}/status`,
   indicando `Sent` o `Delivered` y, opcionalmente, `tracking_number`.
   Al llegar a `Delivered`, entregas publica el aviso RabbitMQ que pedidos consume
   para cerrar el flujo.

Ejemplo de creación de pedido:

```json
{
  "client_id": 1,
  "number_of_pieces": 3,
  "description": "Pedido de prueba",
  "address": "Calle Mayor 10"
}
```

El importe de ese pedido es 30. Un cliente inexistente devuelve 404; con saldo
insuficiente el pedido pasa a `Rejected` y no se solicita fabricación ni entrega. Los pedidos
rechazados por pagos quedan registrados como `Rejected`.

La creación devuelve 202 con `PendingPayment` mientras se procesa el evento.
Si falla un servicio después de cobrar, queda `Pending` con `integration_error`.
Los pasos pendientes se reintentan cada cinco segundos, también tras reiniciar.
Se puede forzar un reintento con `POST /order/{order_id}/retry`.
El identificador de pedido evita repetir cobros o crear entregas duplicadas.
Los pedidos del flujo se conservan; eliminarlos devuelve 409 para evitar
reutilizar identificadores de cobro sin un proceso de cancelación/reembolso.

Pedidos conserva las piezas en su base de datos. `GET /order/{order_id}/pieces`
permite ver su estado. Máquinas recupera las piezas pagadas en cola o en
fabricación desde pedidos y reintenta sus notificaciones si falla la conexión.
Si entregas no puede publicar un aviso de entrega completada, conserva
`order_notified=false` y reintenta la publicación. Ese campo pasa a `true` tras
la confirmación del broker; la actualización del pedido ocurre al consumir
el mensaje. No se vuelve a publicar para entregas ya notificadas anteriormente.

Compose configura las URLs internas usando los nombres de los servicios.
Para desarrollo local, las URLs por defecto utilizan los puertos de la tabla.
El Compose independiente de pedidos accede a los otros servicios por los puertos
del host (`host.docker.internal`). No se deben levantar simultáneamente dos
instancias de pedidos sobre la misma base de datos.

Al arrancar, pedidos y entregas añaden los campos del flujo a bases SQLite
existentes sin borrar datos. Los pedidos antiguos sin cliente y dirección se
conservan y no se envían automáticamente al flujo. Las entregas antiguas también
se conservan, incluso si había duplicados por pedido; la regla que evita duplicar
entregas y las notificaciones automáticas se aplican a las del nuevo flujo.
El índice de cobro requiere que no existan cobros previos duplicados por pedido.

HAProxy publica los cinco servicios bajo las rutas de la sección API Gateway.
