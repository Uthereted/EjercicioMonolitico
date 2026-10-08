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

## Dependencias e imágenes Docker

Cada microservicio instala las librerías de su propio `requirements.txt`.
Docker reutiliza la capa de instalación mientras no cambien ese archivo, la
imagen Python o las instrucciones anteriores del Dockerfile. Cambiar únicamente
el código no requiere reinstalar las dependencias. Evitar `--no-cache` y conservar
la caché de Docker permite aprovechar esta reutilización.

`coloredlogs` se conserva en pedidos y pagos porque su configuración de logs
lo utiliza. `email-validator` es necesario para los correos de clientes.
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

## Flujo REST

1. Crear el cliente con `POST /clients` en clientes.
2. Ingresar saldo con `POST /payment/{client_id}/deposit` en pagos.
3. Crear el pedido con `POST /order` en pedidos. El servicio valida al cliente,
   calcula el importe y solicita el cobro. El precio fijo es **10 unidades de
   saldo por pieza**; no se introduce en la petición.
4. Si el pago se acepta, el pedido solicita la fabricación de sus piezas en
   máquinas y crea la entrega con estado `Pending`.
5. Máquinas notifica el inicio y fin de cada pieza a pedidos mediante
   `PATCH /piece/{piece_id}`. Cuando todas terminan, pedidos marca la entrega
   como `Ready` y el pedido como `ReadyForDelivery`.
6. Actualizar la entrega con `PATCH /deliveries/delivery/{delivery_id}/status`,
   indicando `Sent` o `Delivered` y, opcionalmente, `tracking_number`.
   Al llegar a `Delivered`, entregas notifica a pedidos para cerrar el flujo.

Ejemplo de creación de pedido:

```json
{
  "client_id": 1,
  "number_of_pieces": 3,
  "description": "Pedido de prueba",
  "address": "Calle Mayor 10"
}
```

El importe de ese pedido es 30. Un cliente inexistente devuelve 404; saldo
insuficiente devuelve 409 y no se solicita fabricación ni entrega. Los pedidos
rechazados por pagos quedan registrados como `Rejected`.

Si el cobro no puede confirmarse, el pedido devuelve 202 con `PendingPayment`.
Si falla un servicio después de cobrar, queda `Pending` con `integration_error`.
Los pasos pendientes se reintentan cada cinco segundos, también tras reiniciar.
Se puede forzar un reintento con `POST /order/{order_id}/retry`.
El identificador de pedido evita repetir cobros o crear entregas duplicadas.
Los pedidos del flujo se conservan; eliminarlos devuelve 409 para evitar
reutilizar identificadores de cobro sin un proceso de cancelación/reembolso.

Pedidos conserva las piezas en su base de datos. `GET /order/{order_id}/pieces`
permite ver su estado. Máquinas recupera las piezas pagadas en cola o en
fabricación desde pedidos y reintenta sus notificaciones si falla la conexión.
Si entregas no puede notificar un pedido entregado, conserva `order_notified=false`
y reintenta la notificación al volver el servicio.

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
