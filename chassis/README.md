# Manufacturing Chassis

Librería Python sencilla para compartir infraestructura entre microservicios
FastAPI. Cada servicio importa las funciones que necesita y mantiene su propia
lógica de negocio, base de datos y comunicación con RabbitMQ.

## Repositorio

Ubicación prevista al subir los cambios a GitHub:
https://github.com/Uthereted/EjercicioMonolitico/tree/main/chassis

Repositorio del proyecto: https://github.com/Uthereted/EjercicioMonolitico

El paquete está en `chassis/` dentro del proyecto. También puede copiarse todo
el contenido de esta carpeta a un repositorio independiente sin cambiar la
librería. La publicación y el acceso en GitHub se gestionan al subir la entrega.

## Funcionalidades compartidas

1. `configure_logging(service_name, level="INFO")`: sustituye los archivos
   `logging.ini` duplicados de pedidos y pagos. Escribe en consola con fecha,
   nombre del servicio, nivel, logger y mensaje. Usa la librería estándar, sin
   `coloredlogs`. Configura los loggers `app` y sus hijos, conserva activos los
   loggers de otras librerías y permite cambiar el nivel a `DEBUG` si se necesita.
   Debe llamarse una vez al iniciar cada proceso de servicio.
2. `create_health_router(service_name)`: crea un router con `GET /health` que
   devuelve HTTP 200 y `{"status":"ok","service":"nombre-del-servicio"}`.
   Es una comprobación de respuesta HTTP: no comprueba bases de datos,
   RabbitMQ ni otros servicios.

## Uso por microservicio

| Microservicio | Logs comunes | GET /health del chasis |
| --- | --- | --- |
| Pedidos (`order-service`) | Sí | Sí |
| Pagos (`paymentservice` en Compose) | Sí | Sí |
| Clientes | No | No |
| Entregas | No | No |
| Máquinas | No | No |

El identificador de pagos en logs y en `/health` es `payment-service`.
El endpoint `/` previo de pagos se mantiene por compatibilidad.

## Instalar y usar

Desde esta carpeta (o desde la raíz de un repositorio que contenga este paquete):

```bash
python -m pip install .
```

```python
from fastapi import FastAPI
from manufacturing_chassis import configure_logging, create_health_router

configure_logging("example-service")
app = FastAPI()
app.include_router(create_health_router("example-service"))
```

Los servicios pueden utilizar una sola función; no hay una clase base obligatoria.
El paquete requiere Python 3.10 o superior y FastAPI.

En el proyecto integrado, ejecutar desde `services/order` o `services/payment`:

```bash
python -m pip install -r requirements.txt
```

Esos requisitos instalan `../../chassis`. Docker Compose lo copia e instala
al construir las imágenes; los Dockerfile de pedidos y pagos usan la raíz del
proyecto como contexto de construcción.

## Comprobar

Desde esta carpeta:

```bash
python -m pip install ".[test]"
python -m unittest discover -s tests -v
```

Con el proyecto en ejecución:

```bash
curl http://localhost:13001/health
curl http://localhost:13006/health
```

Estos endpoints se consultan por los puertos de los servicios. El `/health` de
HAProxy en el puerto 8080 sigue comprobando exclusivamente el gateway.

## Entrega

Desde la raíz del proyecto, `python scripts/package_chassis.py` genera
`deliverables/chassis.zip` con este README, el paquete, su configuración y sus
pruebas. No incluye entornos virtuales ni archivos generados de instalación.
