# Manufacturing Microservices

The project is split into five FastAPI microservices: orders, machines,
deliveries, clients and payments. Each service has its own code, container,
Dockerfile and `requirements.txt`.

## Quick start

```bash
git clone <repository-url>
cd EjercicioMonolitico
cp dot_env_example .env        # PowerShell: Copy-Item dot_env_example .env
docker compose up -d --build
curl http://localhost:8080/health
```

Once the containers are up, the whole system is reachable through the API
Gateway at **http://localhost:8080** (see [API Gateway](#api-gateway)).

## Structure

```text
services/
├── client/       # Clients
├── delivery/     # Deliveries
├── machine/      # Machines and manufacturing queue
├── order/        # Orders and pieces
└── payment/      # Payments
haproxy/          # Gateway configuration
compose.yml       # Run everything together
dot_env_example   # Example environment variables
tests/            # Messaging, auth and end-to-end workflow tests
docs/             # Historical documentation (original application)
```

Each service folder contains its code in `app/`, its Dockerfile and its
`requirements.txt` with the dependencies it uses.
The services use `entrypoint.sh` and Hypercorn, except machines, which keeps
`main.py` in its root and starts with Uvicorn.

Orders includes a standalone Compose file at `services/order/compose.yml`,
its volume in `services/order/db_volume/` and documentation in
`services/order/docs/`. Clients uses `db_volume/`, deliveries uses
`delivery_volume/` and payments uses `payment_db_volume/`. The databases
(SQLite) are created automatically on startup if they do not exist.

## Shared chassis

The chassis gathers infrastructure utilities shared by the services:

| Utility | Services |
| --- | --- |
| Logs and `GET /health` | Orders and payments |
| Per-request database session (`create_db_dependency`) | Clients, deliveries, orders and payments |
| Persistent JSON publishing to RabbitMQ (`publish_json_event`) | Orders, payments and deliveries |
| JSON message construction (`create_json_message`) | Pending-event publisher of orders |

JWT verification stays in this project, in orders and payments.
The chassis contains no models, business rules or database commits.
Publishing waits for the broker's confirmation and prepares the queue before
sending; the services still handle retries and mark events as published.

The chassis lives in a separate repository:
[chasis_homework](https://github.com/Uthereted/chasis_homework).
The requirements of clients, deliveries, orders and payments download it from
GitHub. You only need to clone this project; no local chassis folder is
required.

Orders and payments import `configure_logging` and `create_health_router` from
`my_chassis`. Sessions are imported from `my_chassis.database` and publishers
from `my_chassis.messaging`. There is no chassis application to start: it is a
library installed in each service. The separate repository contains the
functions, the installation configuration and a usage README.

To check it after rebuilding and starting the services:

```bash
curl http://localhost:13001/health
curl http://localhost:13006/health
```

The first endpoint returns `{"status":"ok","service":"order-service"}` and the
second `{"status":"ok","service":"payment-service"}`. They do not check the
database or RabbitMQ. The gateway's `/health` on port 8080 is independent.

The dependency uses the chassis `main` branch. After pushing changes to the
chassis, run
`docker compose build --no-cache order-service paymentservice delivery client_service`
and `docker compose up -d order-service paymentservice delivery client_service`
to reinstall it.

## Dependencies and Docker images

Each microservice installs the libraries from its own `requirements.txt`.
Docker reuses the installation layer as long as that file, the Python image or
the preceding Dockerfile instructions do not change. Changing only the code
does not require reinstalling the dependencies. Avoiding `--no-cache` and
keeping the Docker cache lets you take advantage of this reuse.

Clients, deliveries, orders and payments install `microservice_chassis` from
GitHub. Services that publish events select the `[messaging]` extra.
Python imports it as `my_chassis`.
The Dockerfiles include Git and certificates to download it during the build.
On another PC, clone this repository, copy `dot_env_example` to `.env` and run
`docker compose up -d --build`. Outside Docker, install Git and run
`python -m pip install -r requirements.txt` from the service folder.
`email-validator` is required for client emails.
`SQLAlchemy[asyncio]` includes `greenlet`, which is required for asynchronous
database access.

To build all services:

```bash
docker compose build
```

To rebuild and start only orders after changing its code:

```bash
docker compose up -d --build --no-deps order-service
```

If a service's libraries are modified, rebuild its image with
`docker compose up -d --build --no-deps <service-name>`.

## Running with Docker Compose

From the repository root, copy the example file to `.env` if it does not exist
yet. In PowerShell:

```powershell
if (!(Test-Path .env)) { Copy-Item dot_env_example .env }
docker compose up -d --build
```

To validate the configuration, list the containers or stop them:

```bash
docker compose config
docker compose ps
docker compose down
```

To run only orders:

```bash
docker compose -f services/order/compose.yml up -d --build
```

## Accessing the services

| Service | Compose name | Local port | API documentation |
| --- | --- | --- | --- |
| Orders | `order-service` | 13001 | http://localhost:13001/docs |
| Machines | `machineservice` | 13002 | http://localhost:13002/docs |
| Deliveries | `delivery` | 13004 | http://localhost:13004/docs |
| Clients | `client_service` | 13005 | http://localhost:13005/client |
| Payments | `paymentservice` | 13006 | http://localhost:13006/payment |

Other endpoints of the stack:

| Component | URL | Credentials |
| --- | --- | --- |
| API Gateway (HTTP) | http://localhost:8080 | — |
| API Gateway (HTTPS) | https://localhost:8443 | — |
| HAProxy stats | http://localhost:8404 | `admin` / `admin` |
| RabbitMQ management | http://localhost:15672 | `manufacturing` / `manufacturing` |
| RabbitMQ (AMQP) | `localhost:5672` | `manufacturing` / `manufacturing` |

## API Gateway

### Authentication after the merge

Clients keeps a single `POST /clients` route and adds `POST /auth/register`,
`GET /auth/token` (HTTP Basic) and `GET /auth/public-key`. In the gateway they
are `/api/auth/register`, `/api/auth/token` and `/api/auth/public-key`.
Login returns an RS256 JWT valid for one hour. For protected operations, send
`Authorization: Bearer <access_token>`: creating/listing orders and
querying/depositing/charging balance. The other routes keep their previous
behavior; this integration does not add authentication to every API.

For local testing, the initial user `admin` with password `admin123` is kept.
Public registration only creates users with the `user` role. Regular users are
linked to the client with the same email when the token is obtained; the IDs of
the users and clients tables are independent. This is development-grade
authentication: registration does not verify ownership of the email address.

Clients generates the signing key on startup and publishes
`client.cert.updated`. Orders and payments receive that notice on separate
exclusive queues, distinct from `order.events`, and update the public key; they
also fetch it again when they detect an old signature. Restarting clients
invalidates the previous tokens: you must log in again.
The dedicated `order.events` consumer remains the only one that processes
piece and delivery events.

The gateway also listens on `https://localhost:8443` with the local certificate
from `haproxy/gateway.pem`. Port 8080 is kept for HTTP tests.

The tests `tests/test_auth_integration.py` and
`tests/test_workflow_integration.py` use the gateway given by `API_BASE_URL`
(default `http://localhost:8080`). The second one obtains a token first; the
credentials can be changed with `TEST_AUTH_USERNAME` and `TEST_AUTH_PASSWORD`.
`tests/test_auth_verifier.py` is run separately in the orders and payments
environments.

HAProxy gives access to the five services through **http://localhost:8080**.
The individual ports in the table are kept for debugging. Internal calls
between services still use the Docker network.

| Operation | Method | Gateway route |
| --- | --- | --- |
| Gateway status | GET | `/health` |
| Register user | POST | `/api/auth/register` |
| Get token | GET | `/api/auth/token` |
| Get public key | GET | `/api/auth/public-key` |
| Create / list clients | POST / GET | `/api/clients` |
| Get client | GET | `/api/clients/{id}` |
| Deposit balance | POST | `/api/payments/{client_id}/deposit` |
| Get balance | GET | `/api/payments/{client_id}/balance` |
| Create / list orders | POST / GET | `/api/orders` |
| Get order | GET | `/api/orders/{id}` |
| Get order pieces | GET | `/api/orders/{id}/pieces` |
| Retry pending order | POST | `/api/orders/{id}/retry` |
| Get pieces | GET | `/api/pieces` |
| Get machine status | GET | `/api/machine/status` |
| Create / list deliveries | POST / GET | `/api/deliveries` |
| Get delivery | GET | `/api/deliveries/{id}` |
| Update delivery | PATCH | `/api/deliveries/{id}/status` |

JSON bodies and statuses are the same as in the services' own APIs.
For example, a delivery is completed with `{"status":"Delivered"}`.
The older URLs `/api/order/order` and `/api/delivery/delivery` remain available.
Unknown routes return 404. `/health` confirms that the gateway is up;
the availability of each service is checked through its requests and through
the HAProxy statistics at http://localhost:8404 (user and password `admin`).
The Swagger documentation remains on the individual ports in the table.

### Testing the flow manually

With Docker Desktop open, run from the repository root:

```powershell
docker compose up -d
docker compose restart haproxy
```

If the images have not been built yet, use `docker compose up -d --build` in
the first command.

From Postman or Bruno, use `http://localhost:8080` as the base URL and
`Content-Type: application/json` on requests with a body. Save the identifiers
returned by each response:

1. **Create client:** `POST /api/clients`.

   ```json
   {"name":"Test client","email":"test@example.org"}
   ```

2. **Deposit balance:** `POST /api/payments/{client_id}/deposit`.

   ```json
   {"amount":100}
   ```

3. **Create order:** `POST /api/orders`. Replace `client_id` with the client's ID.

   ```json
   {
     "client_id":1,
     "number_of_pieces":3,
     "description":"Manual test",
     "address":"Calle Mayor 10"
   }
   ```

4. **Check manufacturing:** `GET /api/orders/{order_id}` and
   `GET /api/orders/{order_id}/pieces`. Wait until the order is
   `ReadyForDelivery` and the three pieces are `Manufactured`.
   The order returns the `delivery_id`.
5. **Complete delivery:** `PATCH /api/deliveries/{delivery_id}/status`.

   ```json
   {"status":"Delivered","tracking_number":"TEST-001"}
   ```

6. **Verify the result:** `GET /api/orders/{order_id}` must return
   `Delivered`, and `GET /api/payments/{client_id}/balance` must return a
   balance of **70**.

The price is 10 units per piece. Use a different email when creating another
client.

To query the gateway or diagnose a failure:

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod http://localhost:8080/api/orders
docker compose logs --tail=100 haproxy
```

## Local development

Create a virtual environment and install the dependencies of the microservice
you want to run. Use its folder as the working directory, for example
`services/order`.

```bash
cd services/order
pip install -r requirements.txt
hypercorn app.main:app --bind 0.0.0.0:8000
```

Set `SQLALCHEMY_DATABASE_URL` when you need a database other than the default
local one. The orders example is in `services/order/dot_env_example`.

For machines, run from `services/machine`:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## API tests and historical documentation

The orders Bruno collection is in `services/order/docs/bruno_collection/`.
The `Docker Compose` environment uses port `13001`.
Before creating an order, register the client, deposit balance and set
`clientId` in the Bruno environment. The five-piece example requires 50 units.
The machine query uses `machineURL`. The example that updates an order to
`Finished` requires all pieces to be manufactured; deleting an order that is
part of the flow returns 409.
The `API Gateway` Bruno environment lets you run the collection through port
`8080`.

The root `docs/` folder and the legacy UML diagrams preserve historical
documentation of the original application. Their routes and diagrams do not
necessarily represent the current architecture; to test orders, use the
collection mentioned above.

## RabbitMQ and the order.created event

Compose includes RabbitMQ with persistent storage and a management panel at
http://localhost:15672 (development user and password: `manufacturing`).
The AMQP port is `5672`. Orders, payments, machines and deliveries use
`aio-pika`.

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

There is one durable application exchange and four durable queues:
`payment.events` for payments, `order.payment.events` for payment results and
`order.events` for delivery and piece notices to orders, and `machine.events`
for manufacturing requests. A single `order.events` consumer tells both types
apart by their routing key. Delivery creation and the change to `Ready` keep
their current REST calls. Clients does not consume these events.

After the payment is accepted, orders publishes `machine.pieces.requested` with
`{order_id, piece_ids}`. It sets `manufacturing_requested=true` only after the
broker's confirmation; failures stay pending for retry.
Machines publishes `piece.status.update` with `{piece_id, status}`, first
`Manufacturing` and then `Manufactured`, retrying until it receives
confirmation. Orders saves each change before acknowledging the message.
Duplicates do not roll back a finished piece. Machines avoids queuing again
pieces already completed in the current process, even if the REST recovery
still returns them while orders consumes its events. After a restart, it
recovers from orders the paid pieces that are still pending.

Deliveries publishes `delivery.status.update` only when it reaches
`Delivered`, with `{delivery_id, order_id, status}`. Orders consumes
`order.events`, checks that the delivery belongs to the order and applies the
same payment and manufacturing validations as the REST update. It acknowledges
the message after saving the `Delivered` status; processing failures are
requeued and invalid messages are rejected. Receiving the same notice again
does not change the result. `Ready` and `Sent` do not publish events.

The message contains `{order_id, client_id, amount}`. The order itself stores
`order_created_published`, which is only set after the broker's confirmation.
This way publishing is retried if RabbitMQ fails, without an extra table.

The container is configured in `compose.yml`. The `messaging.py` modules of
orders and payments contain the RabbitMQ calls to publish and consume;
Compose alone does not run that Python logic.

Payments consumes `payment.events`, performs the charge and publishes
`payment.approved` or `payment.rejected`, with
`{order_id, client_id, amount, accepted, reason}`. It acknowledges
`order.created` after the broker confirms the publication of the result; if
that fails, it requeues the message with a five-second delay. Orders consumes
`order.payment.events` and acknowledges the result after processing it.
Charges and rejected results are stored per order so that a redelivery neither
changes the result nor charges twice. Messages with a contradictory format or
data are rejected. The payment result is no longer communicated over REST.
Orders keeps the previous endpoint for compatibility, with the same validations
as the consumer.

To start the updated project:

```bash
docker compose up -d --build
docker compose logs -f order-service paymentservice rabbitmq
```

Creating an order returns **202** with `PendingPayment`. Then query
`GET /api/orders/{id}` to learn the outcome; insufficient balance leaves the
order as `Rejected`, with the reason in `integration_error`. Client validation
remains synchronous and returns 404 if the client does not exist.

For development outside Docker, use the `RABBITMQ_URL` and `RABBITMQ_EXCHANGE`
variables from the example file. The standalone orders Compose uses the host's
RabbitMQ and requires payments and the rest of the services to be running.

Tests for persistence, callbacks and duplicate charges (with the orders
dependencies installed):

```bash
python tests/test_order_messaging.py
python tests/test_payment_messaging.py
python tests/test_delivery_messaging.py
python tests/test_machine_messaging.py
```

Full test with all services running and `httpx` installed:

```bash
python tests/test_workflow_integration.py
```

It creates a test client, deposits 100 and orders two pieces; it checks the
manufacturing statuses, the delivery by event and the final balance of 80. It
keeps those records so you can review them. `API_BASE_URL` lets you change the
gateway (default `http://localhost:8080`).

## Order flow

1. Create the client with `POST /clients` in clients.
2. Deposit balance with `POST /payment/{client_id}/deposit` in payments.
3. Create the order with `POST /order` in orders. The service validates the
   client, calculates the amount and publishes `order.created` so that payments
   charges it. The fixed price is **10 balance units per piece**; it is not
   sent in the request.
4. If the payment is accepted, the order requests the manufacturing of its
   pieces from machines and creates the delivery with `Pending` status.
5. Machines reports the start and end of each piece through the
   `piece.status.update` event. When all of them finish, orders marks the
   delivery as `Ready` and the order as `ReadyForDelivery`.
6. Update the delivery with `PATCH /deliveries/delivery/{delivery_id}/status`,
   setting `Sent` or `Delivered` and, optionally, `tracking_number`.
   When it reaches `Delivered`, deliveries publishes the RabbitMQ notice that
   orders consumes to close the flow.

Order status summary:

```text
PendingPayment -> (payment accepted) -> Pending -> ReadyForDelivery -> Delivered
PendingPayment -> (insufficient balance) -> Rejected
```

Example order creation:

```json
{
  "client_id": 1,
  "number_of_pieces": 3,
  "description": "Test order",
  "address": "Calle Mayor 10"
}
```

The amount of that order is 30. A non-existent client returns 404; with
insufficient balance the order becomes `Rejected` and neither manufacturing nor
delivery is requested. Orders rejected by payments are recorded as `Rejected`.

Creation returns 202 with `PendingPayment` while the event is processed.
If a service fails after charging, the order stays `Pending` with
`integration_error`. Pending steps are retried every five seconds, also after a
restart. A retry can be forced with `POST /order/{order_id}/retry`.
The order identifier prevents repeated charges or duplicate deliveries.
Orders in the flow are kept; deleting them returns 409 to avoid reusing charge
identifiers without a cancellation/refund process.

Orders keeps the pieces in its database. `GET /order/{order_id}/pieces` shows
their status. Machines recovers the paid pieces that are queued or being
manufactured from orders and retries its notifications if the connection fails.
If deliveries cannot publish a completed-delivery notice, it keeps
`order_notified=false` and retries the publication. That field becomes `true`
after the broker's confirmation; the order update happens when the message is
consumed. It is not published again for deliveries that were already notified.

Compose configures the internal URLs using the service names.
For local development, the default URLs use the ports in the table.
The standalone orders Compose reaches the other services through the host ports
(`host.docker.internal`). Two orders instances must not be started
simultaneously on the same database.

On startup, orders and deliveries add the flow fields to existing SQLite
databases without deleting data. Old orders without a client and address are
kept and are not automatically sent into the flow. Old deliveries are also
kept, even if there were duplicates per order; the rule that prevents
duplicate deliveries and the automatic notifications apply to those of the new
flow. The charge index requires that there are no previous duplicate charges
per order.

HAProxy publishes the five services under the routes in the API Gateway
section.

## Troubleshooting

- **A service keeps restarting:** check `docker compose logs <service>`. Most
  services depend on RabbitMQ; wait for its healthcheck to pass or run
  `docker compose ps`.
- **The gateway returns 502/503 right after startup:** the backends may not be
  ready yet. Wait a few seconds or run `docker compose restart haproxy`.
- **401 on orders or payments:** the JWT is missing or expired, or clients was
  restarted (this regenerates the signing key). Request a new token at
  `/api/auth/token`.
- **Chassis changes are not picked up:** rebuild the images without cache, as
  described in [Shared chassis](#shared-chassis).
- **Port already in use:** stop whatever is using it or change the left side of
  the port mapping in `compose.yml`.
- **Start from a clean state:** `docker compose down -v` also removes the
  RabbitMQ volume. The SQLite databases live in each service's volume folder
  (`*.db` files are git-ignored); delete them to reset the data.

## Security notes

This project is for development and teaching purposes only:

- The default credentials (`admin` / `admin123`, `manufacturing`, HAProxy stats
  `admin` / `admin`) must be changed before any real deployment.
- The HTTPS certificate and key in `haproxy/` are for local use only; do not
  reuse them elsewhere.
- Registration does not verify email ownership, and RabbitMQ and the databases
  are not hardened.
