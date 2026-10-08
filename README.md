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

La carpeta raíz `docs/` y los diagramas UML heredados conservan documentación
histórica de la aplicación original. Sus rutas y diagramas no representan
necesariamente la arquitectura actual; para probar pedidos, utilizar la
colección indicada arriba.

## Estado de la integración

El flujo completo entre los cinco servicios todavía requiere trabajo: el router
de pedidos contiene conexiones marcadas como futuras, y `CLIENT_SERVICE_URL` en
pagos apunta a `clientservice`, mientras que Compose define `client_service`.
HAProxy publica actualmente pedidos y entregas. La retirada de la aplicación
original conserva el código de los microservicios sin modificar estas integraciones.
