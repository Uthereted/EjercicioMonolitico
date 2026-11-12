# Historial de Sagas

Basado en la página 15 de `06 - Reliability and Observability (Nov2024).pdf`:
identificador, estado, fecha/hora y consultas de historial y últimos estados.
El almacenamiento se realiza en MongoDB por decisión del proyecto.

## Estados que se registran

Se mantienen exactamente los nombres de la Saga coordinada por pedidos:

- `PendingPaymentAndDelivery`
- `PendingPayment`
- `PendingDelivery`
- `PendingConfirmation`
- `Approved`
- `AwaitingCompensation`
- `PendingPaymentRelease`
- `PendingDeliveryCancellation`
- `Suspended`

Ejemplo cuando llega primero la reserva de pago:

`PendingPaymentAndDelivery → PendingDelivery → PendingConfirmation → Approved`

Si llega primero la entrega, el segundo estado es `PendingPayment`. Ante un rechazo,
se registra `AwaitingCompensation`, después el estado de compensación pendiente
que corresponda y finalmente `Suspended`.

`Approved` termina esta Saga de aprobación; la fabricación y la entrega continúan
como estados del pedido. No se añaden estados de Saga llamados `Manufactured`,
`Delivered` o `Completed`.

## Almacenamiento y registro

Colección: **`manufacturing_audit.saga_history`**, en el MongoDB existente.
Cada documento contiene:

```json
{
  "history_id": "order.saga.state_changed:12:3",
  "saga_id": 12,
  "order_id": 101,
  "state": "PendingConfirmation",
  "previous_state": "PendingDelivery",
  "sequence": 3,
  "timestamp": "2026-11-12T10:02:00+00:00",
  "initial_snapshot": false,
  "received_at": "2026-11-12T10:02:01+00:00"
}
```

En MongoDB el identificador se almacena como `_id`; la API lo devuelve como `history_id`.
`timestamp` es la hora UTC del cambio en el coordinador; `received_at` es la hora
en que se recibió para almacenarlo. `sequence` ordena las transiciones de cada
Saga, incluso si se reciben tarde o sus fechas coinciden.

El servicio de pedidos cambia el estado y registra el evento
`order.saga.state_changed` en su outbox dentro de la misma transacción SQLite.
Si la transacción falla, no se confirma ni el cambio ni su registro pendiente.
SQLite conserva únicamente el estado operativo, el contador de secuencia y el
outbox; el historial consultable se guarda en MongoDB.

El evento viaja por `manufacturing.events`, ya que es un hecho sobre la Saga de
un pedido. El Event Logger ya recibe `order.#`: archiva el evento en `business_events`
y escribe su registro en `saga_history`. Solo hace ACK después de guardar ambos.
Las escrituras son idempotentes: una reentrega no añade otra fila al historial.
Si una escritura tiene éxito y la otra falla, el reintento completa la pendiente.

Esto es independiente de **`manufacturing.logs`** y `application_logs`, que siguen
almacenando mensajes internos de las aplicaciones. No hace falta otro contenedor.

La migración añade `history_version` a las Sagas existentes sin borrar datos.
En el primer arranque se registra únicamente el estado que tengan en ese momento,
con `initial_snapshot: true`, `previous_state: null` y la hora de observación actual.
No se reconstruyen estados anteriores ni se atribuyen fechas antiguas. Reiniciar
pedidos no genera otro snapshot de la misma Saga.

## API REST (solo administradores)

| Consulta | Ruta por HAProxy |
| --- | --- |
| Historial de todas las Sagas | `GET /api/orders/sagas/history` |
| Historial de un pedido | `GET /api/orders/{order_id}/saga/history` |
| Último estado de cada Saga | `GET /api/orders/sagas/latest` |

La última consulta admite `?order_id=101` para consultar únicamente el último
estado de la Saga de ese pedido. Las rutas directas del servicio sustituyen
`/api/orders` por `/order`, en el puerto 13001.

Se exige un JWT válido y rol `admin`. Sin token: 401. Con un usuario sin ese rol:
403. Si MongoDB no está disponible: 503, además del bloqueo por la infraestructura
de logs ya configurado.

Filtros opcionales: `order_id` (en las consultas generales), `state`, `date_from`
y `date_to`. Las fechas deben incluir zona horaria y los límites son inclusivos.
Paginación: `limit` (por defecto 50, máximo 200) y `offset` (por defecto 0).

La respuesta tiene forma `{"items": [...], "total": 123, "limit": 50, "offset": 0}`.
El historial de un pedido se devuelve en orden de secuencia ascendente; el listado
general se devuelve de más reciente a más antiguo. En `/latest`, los filtros de
estado y fecha se aplican sobre el último registro real de cada Saga, sin recuperar
un estado antiguo que coincida con el filtro.

El guardado en MongoDB es asíncrono. Una consulta inmediatamente después de crear
o cambiar un pedido puede no incluir todavía su último cambio; repítela unos segundos
después. Si no hay registros coincidentes se devuelve una página vacía, no un 404.
Los pedidos antiguos que nunca tuvieron una `OrderSaga` no reciben una Saga inventada.

## Pasos manuales (PowerShell)

No se han ejecutado tests ni iniciado contenedores durante esta implementación.

### 1. Aplicar los cambios y obtener token

Desde la raíz del proyecto, con Docker Desktop abierto y `.env` preparado:

```powershell
docker compose up -d --build
```

Espera a que los backends estén UP en `http://localhost:8404` (`admin` / `admin`).
Después:

```powershell
$basic = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('admin:admin123'))
$tokens = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/auth/token -Headers @{Authorization = "Basic $basic"}
$headers = @{Authorization = "Bearer $($tokens.access_token)"}
```

### 2. Consultar Sagas existentes

```powershell
$history = Invoke-RestMethod 'http://localhost:8080/api/orders/sagas/history?limit=20' -Headers $headers
$history.items | Format-Table order_id, saga_id, sequence, state, timestamp, initial_snapshot

$latest = Invoke-RestMethod 'http://localhost:8080/api/orders/sagas/latest?limit=20' -Headers $headers
$latest.items | Format-Table order_id, saga_id, state, timestamp
```

Las Sagas previas al cambio deben tener un registro inicial marcado como snapshot.
Si el entorno está vacío, crea un pedido en el paso siguiente.

### 3. Crear una Saga nueva con resultado Approved

Registra un cliente nuevo; cambia el nombre de usuario y email en cada repetición:

```powershell
$suffix = [Guid]::NewGuid().ToString('N')
$body = @{username = "history-$suffix"; password = 'manual123'; name = 'Saga history'; email = "history-$suffix@example.org"; address = 'Calle Mayor 10'; postal_code = '01001'} | ConvertTo-Json
$customer = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/auth/register -ContentType 'application/json' -Body $body
```

Espera unos segundos a que los servicios reciban `client.created`. Deposita saldo
y crea un pedido de una pieza:

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8080/api/payments/$($customer.id)/deposit" -Headers $headers -ContentType 'application/json' -Body '{"amount":100}'
$orderBody = @{client_id = $customer.id; number_of_pieces = 1; description = 'Comprobacion de historial'} | ConvertTo-Json
$order = Invoke-RestMethod -Method Post -Uri http://localhost:8080/api/orders -Headers $headers -ContentType 'application/json' -Body $orderBody
```

Si el depósito o el pedido devuelve 404 porque el catálogo aún no se ha actualizado,
espera y repite únicamente esa petición. Después consulta varias veces:

```powershell
$page = Invoke-RestMethod "http://localhost:8080/api/orders/$($order.id)/saga/history" -Headers $headers
$page.items | Format-Table sequence, state, previous_state, timestamp, initial_snapshot

Invoke-RestMethod "http://localhost:8080/api/orders/sagas/latest?order_id=$($order.id)" -Headers $headers
```

El historial debe terminar en `Approved` y contener las cuatro transiciones
indicadas arriba. El estado intermedio dependerá de qué respuesta llegue primero.
Los registros de una Saga nueva deben llevar `initial_snapshot: false`.

### 4. Comprobar compensación, filtros y permisos

Crea otro cliente y pedido repitiendo el paso anterior **sin depositar saldo**.
Su Saga debe pasar por compensación hasta `Suspended`.

```powershell
Invoke-RestMethod 'http://localhost:8080/api/orders/sagas/latest?state=Suspended&limit=10&offset=0' -Headers $headers
Invoke-RestMethod 'http://localhost:8080/api/orders/sagas/history?state=PendingConfirmation&limit=10' -Headers $headers
curl.exe -i http://localhost:8080/api/orders/sagas/history
```

La consulta sin token debe devolver 401. Obtén un token del usuario recién registrado
y repite las consultas: deben devolver 403. Una fecha sin zona horaria, `limit=201`
o un nombre de estado inexistente deben devolver 422.

### 5. Comprobar MongoDB y ausencia de duplicados

```powershell
docker compose exec mongodb mongosh
```

Dentro de MongoDB, cambia `101` por el ID de tu pedido:

```javascript
use manufacturing_audit
db.saga_history.find({order_id:101}).sort({sequence:1})
db.saga_history.countDocuments({order_id:101})
```

Sal de MongoDB. Reinicia pedidos y vuelve a contar:

```powershell
docker compose restart order-service
```

El número de registros no debe aumentar solo por el reinicio. Puedes detener el
Event Logger, crear un pedido y volver a arrancarlo: los registros pendientes se
almacenarán al consumir el outbox y la cola de auditoría.

## Tests creados, sin ejecutar

Se han creado siete tests offline: transiciones reales sin duplicados, rollback
del cambio si falla el registro, snapshot único de Sagas existentes, reentrega si
falla MongoDB, idempotencia del almacenamiento, rol administrador y respuesta 503
si no se puede consultar MongoDB.

Con la wheel del chasis y las dependencias de pedidos y Event Logger instaladas:

```powershell
.\.venv\Scripts\python.exe tests/test_saga_history.py -v
```

El resultado esperado es `Ran 7 tests` y `OK`; no se ha comprobado ejecutándolos.
Estos tests no necesitan contenedores. Usa el Compose principal para las pruebas
manuales: el Compose aislado de pedidos requiere una URL MongoDB alcanzable mediante
`MONGODB_URL`, ya que el MongoDB del stack principal no publica el puerto 27017.
