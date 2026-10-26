# Manufacturing Microservices

The project is split into five FastAPI microservices: orders, machines,
deliveries, clients and payments. Each service has its own code, container,
Dockerfile and `requirements.txt`.

## Quick start

```bash
git clone <repository-url>
cd EjercicioMonolitico
cp dot_env_example .env
docker compose up -d --build
curl http://localhost:8080/health
```

Once the containers are up, the whole system is reachable through the API
Gateway at **http://localhost:8080** (see [API Gateway](#api-gateway)).

## Structure

```text
services/
â”œâ”€â”€ client/       # Clients
â”œâ”€â”€ delivery/     # Deliveries
â”œâ”€â”€ machine/      # Machines and manufacturing queue
â”œâ”€â”€ order/        # Orders and pieces
â””â”€â”€ payment/      # Payments
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

JWT verification stays in the individual services in this project.
The chassis contains no models, business rules or database commits.
It also provides `publish_json_event` and `create_json_message`; the current
transactional event flow uses each service's `app.events` module to keep its
outbox/inbox in the same database transaction as its business state.

The chassis lives in a separate repository:
[chasis_homework](https://github.com/Uthereted/chasis_homework).
The requirements of clients, deliveries, orders and payments download it from
GitHub. You only need to clone this project; no local chassis folder is
required.

Orders and payments import `configure_logging` and `create_health_router` from
`my_chassis`. Sessions are imported from `my_chassis.database`.
There is no chassis application to start: it is a
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
| API Gateway (HTTP) | http://localhost:8080 | â€” |
| API Gateway (HTTPS) | https://localhost:8443 | â€” |
| HAProxy stats | http://localhost:8404 | `admin` / `admin` |
| RabbitMQ management | http://localhost:15672 | `manufacturing` / `manufacturing` |
| RabbitMQ (AMQP) | `localhost:5672` | `manufacturing` / `manufacturing` |

## API Gateway

### Authentication after the merge

Clients keeps a single `POST /clients` route and adds `POST /auth/register`,
`POST /auth/token` (HTTP Basic; `GET` remains compatible) and `GET /auth/public-key`. In the gateway they
are `/api/auth/register`, `/api/auth/token` and `/api/auth/public-key`.
Login returns an RS256 JWT valid for one hour. For protected operations, send
`Authorization: Bearer <access_token>`: creating/reading/listing clients, creating/listing orders and
querying/depositing/charging balance, all machine operations, and delivery CRUD
and status changes. Delivery's health endpoint stays public. The other routes keep their previous
behavior; this integration does not add authentication to every API.

Machine and delivery validate RS256, issuer `manufacturing-client`, audience
`manufacturing-services` and required claims `exp`, `iat`, `sub`. Missing or invalid
tokens return 401; an unavailable signing key returns 503. They refresh cached
keys after a signature failure and on persisted `auth.signing-key.published` facts.

Internal services now exchange domain facts only. There is no HTTP/RPC
request/reply between services. Orders and payments validate clients using local
catalogues maintained by client events. Each JWT consumer keeps a persistent
public key received through signing-key publication events.

Newly created clients become visible in the other services asynchronously.
A request that arrives before its catalogue update may return 404; retry shortly.
Periodic client/public-key snapshots also bootstrap new or recovered databases.
The optional service-token HTTP API is retained for external compatibility but
is not used by the internal choreography.

Users may create a client only for the email in their signed token, read that
client, and list only their own client. Admins may create and read all clients.
For self-registration: register, request a token with Basic credentials, create
your client with Bearer authentication, then request a fresh token to include the
new `client_id` for orders/payments. Registration and the public-key endpoint stay
public; obtaining a token always requires valid Basic credentials.

User login now returns `access_token`, `refresh_token`, `token_type`,
`expires_in` (3600 seconds) and `refresh_expires_in` (seven days on login).
`POST /auth/refresh` accepts JSON `{"refresh_token":"..."}` without Basic or an
access token and returns a new pair. Save both new values: each refresh is single
use. Refresh sessions have an absolute seven-day lifetime from login; rotation
does not extend it. Reusing a consumed refresh token revokes that login's entire
refresh session, including its latest token. Serialize refresh calls in clients.
`POST /auth/logout` accepts the same JSON and revokes that refresh session.
Already issued access JWTs remain valid until expiry; logout prevents renewal.
The gateway paths are `/api/auth/refresh` and `/api/auth/logout`.

Refresh tokens are random opaque values, stored only as SHA-256 hashes in the
`refresh_tokens` table in the existing client database. Startup creates that new
table without deleting existing data. Sessions persist across restarts and
renew with the current user roles, client association and signing key. Service
tokens still use service credentials and do not receive refresh tokens.

Bruno includes `Auth / Request token`, `Auth / Refresh token`, `Auth / Logout`
and `Client / List clients`. Select the
`Gateway` environment, set `authUsername` and secret `authPassword`, then run
`Request token`; it stores runtime variables `accessToken` and `refreshToken`.
`Refresh token` replaces both, and `Logout` clears them after revoking the session.
The client folder inherits Bearer authentication from `accessToken`.

For local JWT regression tests, set `PYTHONPATH` to `services/machine` or
`services/delivery` and run `python -m unittest discover -s tests -p test_auth_verifier.py -v`.
Run `tests/test_service_jwt_routes.py` separately with `AUTH_TEST_SERVICE=machine`
and `AUTH_TEST_SERVICE=delivery`; run `tests/test_service_token.py` for service login.

For local testing, the initial user `admin` with password `admin123` is kept.
Public registration only creates users with the `user` role. Regular users are
linked to the client with the same email when the token is obtained; the IDs of
the users and clients tables are independent. This is development-grade
authentication: registration does not verify ownership of the email address.

Client generates its signing key on startup and periodically publishes its
public key as a fact. Subscribers persist that key locally. Restarting client
changes its signing key; existing access tokens then need renewal or a new login.
Persistent opaque refresh sessions survive this change.

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
| Get token (HTTP Basic) | POST / GET | `/api/auth/token` |
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

## Domain events and recovery

All internal communication uses the durable topic exchange
`manufacturing.events`. External API calls still enter through HAProxy.
Publishers record facts about their own state; subscribers independently decide
what to do. There are no internal command queues, RPC operations or reply queues.

| Fact | Publisher | Subscribers and local reaction |
| --- | --- | --- |
| `client.created` / `clients.catalogue.published` | Client | Order/payment update their local client catalogues |
| `auth.signing-key.published` | Client | JWT consumers persist the RSA public key |
| `order.created` | Order | Payment decides and records the payment outcome |
| `payment.approved` / `payment.rejected` | Payment | Order records the outcome |
| `order.paid` | Order | Machine persists work; delivery independently creates a delivery |
| `piece.manufacturing.started` / `piece.manufactured` | Machine | Order updates its local piece state |
| `order.manufactured` | Order | Delivery independently makes the shipment ready |
| `delivery.created` / `delivery.ready` / `delivery.delivered` | Delivery | Order updates its local view of delivery progress |

Each service stores an `event_outbox` and `event_inbox` in its own SQLite
database. State changes, derived facts and inbox identifiers commit atomically.
The outbox publisher retries outages, uses persistent messages and broker
confirmations. A crash between confirmation and marking an event published may
produce a duplicate; subscribers persist event identifiers and process effects
only once. Invalid facts are rejected; transient failures roll back and retry.

The durable queues are `order.domain`, `payment.domain`,
`delivery.domain` and `machine.domain`. Infrastructure provisions their
bindings before publishing, so an offline subscriber can catch up later.

Machine has its own SQLite database on the `machine_data` volume. Both queued
and interrupted work survive restarts. It never polls orders for recovery.
Payment outcomes, including rejections, remain stable after duplicate events.
Delivery creation is unique per workflow order and cannot regress after a late
paid-order fact. Existing active orders are bridged to the new event stream on
startup without charging them twice. Legacy API publication flags are retained
for compatibility; the outbox is now the publication authority.

To run the stack and regression tests:

```powershell
docker compose up -d --build
python tests/test_order_messaging.py
python tests/test_payment_messaging.py
python tests/test_delivery_messaging.py
python tests/test_machine_messaging.py
python tests/test_auth_integration.py
python tests/test_workflow_integration.py
```

The live tests create retained test records. The workflow test verifies a paid
order reaches `Delivered` with the expected final balance.

## Order flow

1. Create the client with `POST /clients` in clients.
2. Deposit balance with `POST /payment/{client_id}/deposit` in payments.
3. Create the order with `POST /order` in orders. The service validates the
   client in its local catalogue, calculates the amount and publishes
   `order.created`. Payment independently evaluates that fact. The fixed price is **10 balance units per piece**; it is not
   sent in the request.
4. After accepting the payment outcome, order publishes `order.paid`. Machine
   persists its work and delivery creates a pending shipment independently.
5. Machines reports the start and end of each piece through the
   manufacturing facts. Once all pieces finish, order publishes
   `order.manufactured`. Delivery makes its shipment ready and publishes
   `delivery.ready`; order observes that fact and becomes `ReadyForDelivery`.
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
`integration_error`. Pending outbox publications are retried after outages, also after a
restart. A retry can be forced with `POST /order/{order_id}/retry`.
The order identifier prevents repeated charges or duplicate deliveries.
Orders in the flow are kept; deleting them returns 409 to avoid reusing charge
identifiers without a cancellation/refund process.

Orders keeps the pieces in its database. `GET /order/{order_id}/pieces` shows
their status. Machines recovers the paid pieces that are queued or being
manufactured from orders and retries its notifications if the connection fails.
If deliveries cannot publish a completed-delivery notice, it keeps
its event in the local outbox and retries publication. The order update happens
when its subscriber commits the received fact.

Compose configures the shared RabbitMQ connection using the broker service name.
For local development, set `RABBITMQ_URL` to the broker's exposed AMQP port.
The standalone orders Compose reaches RabbitMQ on `host.docker.internal:5672`;
the other services must connect to that same broker and virtual host.
Two orders instances must not be started
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
