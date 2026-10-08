# Manufacturing Microservices

El proyecto está dividido en cinco microservicios FastAPI: pedidos, máquinas,
entregas, clientes y pagos. Cada servicio tiene su propio código y dependencias.

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

Cada carpeta de servicio contiene `app/`, `Dockerfile` y `requirements.txt`.
Los servicios usan `entrypoint.sh` y Hypercorn, salvo máquinas, que mantiene
`main.py` en su raíz y arranca con Uvicorn.

Pedidos incluye un Compose independiente en `services/order/compose.yml`,
su volumen en `services/order/db_volume/` y documentación en `services/order/docs/`.
Clientes utiliza `db_volume/`, entregas `delivery_volume/` y pagos
`payment_db_volume/`. Estos datos se conservan.

## Ejecutar con Docker Compose

Desde la raíz del repositorio, copiar el archivo de ejemplo a `.env` si todavía
no existe. En PowerShell:

```powershell
Copy-Item dot_env_example .env
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

HAProxy escucha en el puerto `8080` y enruta `/api/order` a pedidos y
`/api/delivery` a entregas. Su panel de estadísticas está en el puerto `8404`.

## Desarrollo local

Crear un entorno virtual e instalar las dependencias del servicio que se vaya a
ejecutar. El directorio de trabajo y la raíz de fuentes deben ser la carpeta
de ese servicio, por ejemplo `services/order`.

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

HAProxy mantiene sus rutas actuales para pedidos y entregas. Los otros servicios
se usan directamente por sus puertos; publicar más rutas queda fuera de esta fase.

## Verificar el flujo

Las pruebas de `tests/test_rest_flow.py` utilizan las cinco aplicaciones FastAPI,
transporte HTTP ASGI y bases SQLite temporales; no acceden a los volúmenes reales.
Con Python 3.11 o superior, instalar las dependencias y ejecutar:

```bash
python -m pip install -r tests/requirements.txt
python -m pytest tests/test_rest_flow.py -q
```
