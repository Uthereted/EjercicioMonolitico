# Monolithic application 

## Summary

The manufacturing monolith has been split into five FastAPI microservices (clients, orders, payments, machine and deliveries), each with its own SQLite database, behind an HAProxy API Gateway reachable over HTTP and HTTPS. Internal Saga commands and business facts use RabbitMQ. Read operations are separated from commands; orders and payments query the client service through Consul-discovered HTTPS calls with the original user JWT. An event logger archives every business fact in MongoDB. Shared infrastructure (JWT verification, a transactional outbox and inbox, broker access and database setup) lives in the `microservice_chassis` library, and every API is protected with JWT tokens issued by the client service.

This document describes the resulting architecture, authentication, the chassis, the event contracts, the reliability guarantees, and how to deploy and test the system. It reflects [Uthereted/EjercicioMonolitico](https://github.com/Uthereted/EjercicioMonolitico) at commit `7feec07`.

## Architecture overview

The system runs as eleven containers: the HAProxy gateway, Consul, RabbitMQ, the five business microservices, the event logger, the log aggregator and MongoDB. Saga commands and business facts travel through RabbitMQ; client queries use direct HTTPS addresses obtained from Consul. All six log producers use cached readiness from the background logging monitor before operations. If application logging becomes unavailable, new HTTP operations return 503 and background business operations pause until recovery. The event logger and application log aggregator have separate queues and MongoDB collections.

| Container | Role | Storage |
| --- | --- | --- |
| `haproxy` | API Gateway on `:8080` (HTTP) and `:8443` (HTTPS) | None |
| `rabbitmq` | Topic exchange `manufacturing.events` and five durable queues | RabbitMQ volume |
| `client_service` | Clients and authentication | `client.db` |
| `order-service` | Orders and pieces | `orders.db` |
| `paymentservice` | Accounts and charges | `payment.db` |
| `machineservice` | Piece manufacturing | `machine.db` |
| `delivery` | Deliveries | `delivery.db` |
| `event-logger` | Business-event audit worker | Writes to MongoDB |
| `log-aggregator` | Application log consumer and admin REST API | MongoDB `application_logs` |
| `mongodb` | Audit store | `manufacturing_audit` database |

## Microservices

Each service lives in `services/<name>/`, has its own Dockerfile and is the sole owner of its data: no service reads another service's database. Services receive business facts and signing keys through events. Orders and payments read authoritative client details through a Consul-discovered HTTPS query.

**Clients** (`client_service`, port 13005) manages clients and is also the authentication service. It stores clients, users, roles and refresh sessions in `client.db`, issues JWT access and refresh tokens, and publishes `client.created`, `client.deleted` and `auth.signing-key.published`. Client GET handlers and their read model are separate from registration/deletion commands.

**Orders** (`order-service`, port 13001) owns orders and pieces in `orders.db`. Creation queries the current client profile through Consul and starts the explicit approval Saga. Order/piece GET handlers live in `order_queries.py` and enforce JWT ownership. Saga commands and responses continue through RabbitMQ.

**Payments** (`paymentservice`, port 13006) manages accounts, deposits and charges in `payment.db`. It reacts to `order.created` by charging the client once and publishes `payment.approved` or `payment.rejected`.

**Machine** (`machineservice`, port 13002) now has its own database, `machine.db`. It reacts to `order.paid` by persisting the work, manufactures the pieces one at a time and publishes `piece.manufacturing.started` and `piece.manufactured`. Queued and interrupted work survives restarts without asking any other service.

**Deliveries** (`delivery`, port 13004) manages deliveries in `delivery.db`. It creates a pending delivery when it sees `order.paid`, marks it `Ready` on `order.manufactured`, and publishes `delivery.created`, `delivery.ready` and `delivery.delivered`.

**Event logger** (`event-logger`) is an independent worker with an internal HTTP health endpoint on port 8000. It consumes the `audit.domain` queue and stores every business fact in MongoDB (`manufacturing_audit.business_events`). Neither its health port nor MongoDB has a published host port.

## Authentication

The client service also acts as the authentication service. It issues RS256 access tokens (a 2048-bit RSA key) that expire after one hour, together with refresh tokens. All five APIs verify access tokens locally with the chassis's `JWTVerifier`: signature, issuer `manufacturing-client`, audience `manufacturing-services` and the required claims `exp`, `iat` and `sub`. A missing or invalid token returns 401; if no public key is available yet, 503.

| Client service endpoint | Gateway route | Purpose |
| --- | --- | --- |
| `POST /auth/register` | `/api/auth/register` | Registers a user. Public registration only allows the `user` role. |
| `POST /auth/token` (also `GET`) | `/api/auth/token` | Login with HTTP Basic. Returns `access_token`, `refresh_token`, `expires_in` (3600 s) and `refresh_expires_in` (7 days). |
| `POST /auth/refresh` | `/api/auth/refresh` | Exchanges a refresh token for a new pair. Each refresh token works once. |
| `POST /auth/logout` | `/api/auth/logout` | Revokes the refresh session. |
| `GET /auth/public-key` | `/api/auth/public-key` | PEM public key. |

The access token contains `sub`, `username`, `email`, `roles`, `client_id` and a unique `jti`. The `client_id` is found by looking up the client with the same email as the user. Passwords are stored with bcrypt. On startup the service creates the `admin` and `user` roles and an `admin` / `admin123` user for testing.

**Refresh sessions.** Refresh tokens are random opaque values stored only as SHA-256 hashes in the `refresh_tokens` table. Every refresh rotates the pair, and the session has an absolute lifetime of seven days from login that rotation does not extend. Reusing an already consumed refresh token revokes the whole session, which protects against stolen tokens. Logout revokes the session; access tokens already issued stay valid until they expire.

**Authorization.** A Bearer token is required for: creating, reading and listing clients; creating and listing orders; depositing, charging and checking balances; every machine operation; and every delivery operation except its service check. A regular user can only create a client for the email in their own token, see their own client, and order and pay for their own `client_id` (403 otherwise). The `admin` role can act on any client.

**Key distribution.** The client service generates its signing key in memory at startup and publishes it periodically as the `auth.signing-key.published` fact. Every JWT consumer stores the key in its own database, so protected requests keep working even while the client service is offline. Consumers also reload the key when a token's signature does not match. Restarting the client service changes the key: existing access tokens must be renewed, but refresh sessions survive.

This remains development-grade authentication: registration does not verify email ownership, and the default credentials must be changed before any real deployment.

## Microservice chassis

The chassis is the `microservice_chassis` 0.8.0 library, imported in Python as `my_chassis`. Its independent source repository is [Uthereted/chasis_homework](https://github.com/Uthereted/chasis_homework). It is used by all five services and the event logger, and it holds the infrastructure that the choreography needs everywhere, so each service keeps only its business logic.

| Module | What it provides | Used by |
| --- | --- | --- |
| `my_chassis.auth.JWTVerifier` | RS256 verification of issuer, audience and required claims, with a cached public key refreshed after a signature mismatch | All five APIs |
| `my_chassis.keys` | Validation of RSA public keys, a loader that reads the key from the service's database, and persistence of keys received as facts | JWT consumers |
| `my_chassis.events.EventBus` | `event_outbox`, `event_inbox` and signing-key tables bound to the service's own database; atomic recording of facts; confirmed publishing with retries; one-time processing of received facts | The five services |
| `my_chassis.messaging.TopicTransport` | Broker connection, the topic exchange and the subscription of a service's own queue to its topics | Services and event logger |
| `my_chassis.discovery` | Consul registration, deregistration and healthy service discovery | All seven Python services |
| `my_chassis.database` | `create_database` (engine and session factory) and the per-request session dependency | All five service SQLite databases (MongoDB uses its own client) |
| `my_chassis.observability.install_logging` (project settings in `service_logging`) | RabbitMQ application logging and fail-closed operation gates | All six log producers |
| `create_health_router` | `GET /service/health` using the `/service` router prefix | All five APIs; orders and payments also retain `/health` |

Each service's `app/events.py` only binds the shared `EventBus` to its own database and configuration, and each JWT module supplies its issuer, audience and key loader. Business models, event contracts, permissions, payment decisions, workflow rules and refresh sessions stay in the services.

**Version 0.8.0 validation:** tests are pending manual execution; see `vendor/README.md`.

**Distribution.** The repository ships a versioned wheel, `vendor/microservice_chassis-0.8.0-py3-none-any.whl`, and every service pins `microservice_chassis[auth,events,database,observability]==0.8.0`. Docker installs that exact file first, so the stack starts without network access to GitHub or a mutable branch. `tools/build_chassis.py` rebuilds the wheel from a local checkout of the chassis repository:

```bash
python tools/build_chassis.py --source ../chasis_homework
```

For local development, install the wheel from the repository root:

```bash
python -m pip install "vendor/microservice_chassis-0.8.0-py3-none-any.whl[auth,events,database]"
```

**Health check scope.** All seven application services expose unauthenticated
`GET /service/health`. HTTP 200 with `{"status":"ok","service":"<service-name>"}`
requires healthy dependencies; unknown, failed or expired results return 503.
The five business APIs use the chassis `HealthMonitor` to probe SQL, the JWT public
key and background tasks, and read the live connections/channels of their RabbitMQ
publisher/consumers and cached logging health. Orders also probes its history MongoDB.
Audit checks its MongoDB and actual consumer connection. The log aggregator caches
RabbitMQ and MongoDB read/write readiness, with a four-second maximum age. Its health
covers ingestion, so that client key publication can bootstrap before log-query JWT
verification becomes available. Endpoint handlers only read cached state; probes run
in background tasks. Orders/payments `/health` and delivery `/deliveries/` follow the
same readiness criteria. Gateway `/health` requires all seven backends to be available.


HAProxy sends `GET /service/health` directly to each container on port 8000 and expects exactly HTTP 200. All backends inherit the circuit breaker settings in `default-server`, following the example on page 13 of the course's Reliability and Observability slides:

| Parameter | Value | Behavior |
| --- | --- | --- |
| `inter` | `5s` | Interval between checks while healthy |
| `downinter` | `1s` | Interval between checks while DOWN |
| `fastinter` | `500ms` | Interval during transitions towards DOWN or UP |
| `fall` | `2` | Consecutive failures required to mark the server DOWN |
| `rise` | `3` | Consecutive successes required to restore the server to UP |

Once a server is DOWN, HAProxy stops directing requests to it but keeps checking for recovery. Since each API currently has only one instance, its gateway routes return HTTP 503 while that instance is DOWN. Other backends remain available. These are health-check failures, not a count of business-request errors. The event logger has a monitoring-only backend with no public gateway route. The gateway's `/health` also returns 503 when any required backend is unavailable.

### Manual health checks (PowerShell)

Rebuild and start the services from the repository root (requires Docker Desktop and the project's `.env`):

```powershell
docker compose up -d --build
foreach ($port in 13001, 13006, 13004, 13005, 13002, 13007) {
    Invoke-RestMethod "https://localhost:$port/service/health"
}
docker compose exec event-logger python -c "import urllib.request; print(urllib.request.urlopen('https://localhost:8000/service/health').read().decode())"
```

Each request should return `status: ok` and its service name without a token. Open HAProxy statistics at `https://localhost:8404` (credentials `admin` / `admin`) and confirm all seven backends are UP with HTTP checks reporting 200.

To observe failure detection, stop orders and allow around ten seconds for checks:

```powershell
docker compose stop order-service
Invoke-WebRequest https://localhost:8080/api/orders
```

The order server should show DOWN and the gateway should return 503 (PowerShell reports an HTTP error). Other backends should remain UP. Restore orders:

```powershell
docker compose start order-service
```

After application startup and three consecutive successful checks, orders should show UP again. While DOWN, the first recovery check runs at the one-second interval, and the following transition checks run every 500ms. `https://localhost:8080/health` returns 503 until all required backends have recovered.

If only `haproxy/haproxy.ctmpl` has changed, apply the configuration with `docker compose restart haproxy`. No application rebuild is needed for an interval change.

## RabbitMQ: queues and event contracts

Domain facts use the durable **topic** exchange `manufacturing.events`. Saga commands and responses use `manufacturing.commands` and `manufacturing.saga.responses`. Application logging uses the new, separate `manufacturing.logs` exchange. No application log is published to the domain or saga exchanges. The aggregator subscribes to public-key events only for local JWT verification; those are stored separately from logs.

See [Log aggregation and manual checks](docs/log-aggregation.md) for log fields, the REST API, failure handling and manual verification steps.

![Messaging topology](docs/project_messaging.png)

RabbitMQ imports its topology at boot from `infrastructure/rabbitmq/domain-definitions.json` (configured in `infrastructure/rabbitmq/20-domain.conf`), before any service publishes. Publishers only declare the exchange and never create other services' queues; each subscriber declares its own queue and binds its `messaging.TOPICS`. Adding a new consumer therefore does not require changing or rebuilding any publisher.

| Queue | Consumer | Bound routing keys |
| --- | --- | --- |
| `order.domain` | Orders | `client.created`, `payment.approved`, `payment.rejected`, `piece.manufacturing.started`, `piece.manufactured`, `delivery.created`, `delivery.ready`, `delivery.delivered`, `auth.signing-key.published` |
| `payment.domain` | Payments | `client.created`, `order.created`, `auth.signing-key.published` |
| `machine.domain` | Machine | `order.paid`, `auth.signing-key.published` |
| `delivery.domain` | Deliveries | `order.paid`, `order.manufactured`, `auth.signing-key.published` |
| `audit.domain` | Event logger | `client.#`, `order.#`, `payment.#`, `piece.#`, `delivery.#` |

The system uses 13 fact types:

| Fact | Publisher | Data | Reaction |
| --- | --- | --- | --- |
| `client.created` | Clients | `{client_id}` | Orders and payments add it to their local client catalogue |
| `auth.signing-key.published` | Clients | `{public_key}` | Every JWT consumer stores the public key |
| `order.created` | Orders | `{order_id, client_id, amount}` | Payments decides and records the charge |
| `order.saga.state_changed` | Orders | `{saga_id, order_id, state, previous_state, sequence, timestamp, initial_snapshot}` | Event Logger appends idempotent MongoDB Saga history |
| `payment.approved` / `payment.rejected` | Payments | `{order_id, client_id, amount, accepted, reason}` | Orders records the outcome |
| `order.paid` | Orders | `{order_id, client_id, amount, address, piece_ids, manufactured_piece_ids}` | Machine persists the work; deliveries creates a pending delivery |
| `piece.manufacturing.started` / `piece.manufactured` | Machine | `{piece_id, order_id}` | Orders updates the piece |
| `order.manufactured` | Orders | same as `order.paid` | Deliveries makes the delivery `Ready` |
| `delivery.created` / `delivery.ready` / `delivery.delivered` | Deliveries | `{order_id, client_id, delivery_id}` | Orders updates its view of the delivery |

Every message carries the same envelope, `{event_id, event_type, producer, data}`, is persistent, and uses the `event_id` as `message_id` (for example `order.paid:12`). Each consumer validates `data` against strict Pydantic contracts defined in its own `contracts.py`; it never imports another service's code.

The consumer then decides:

- **ack** once the fact and its effects are committed, or if its `event_id` is already in the inbox (a duplicate).
- **reject without requeue** if the envelope or data is invalid. Retrying would never fix it.
- **nack with requeue**, after one second, for transient errors; the transaction is rolled back first.

## Order flow

The order coordinator also records its real Saga transitions in MongoDB
`manufacturing_audit.saga_history`. History is staged transactionally in the
coordinator's outbox and stored by the Event Logger. Admin-only APIs provide all
history, one order's history and the latest states, with filters and pagination.
Existing Sagas get a single snapshot of their current state. See
[Saga history and manual checks](docs/saga-history.md).

| Step | Trigger | Fact or request | Order status |
| --- | --- | --- | --- |
| 1 | User | `POST /order` with JWT | `PendingPayment` |
| 2 | Orders | `order.created` | `PendingPayment` |
| 3 | Payments | `payment.approved` (or `payment.rejected` → `Rejected`) | `Accepted` |
| 4 | Orders | `order.paid`, consumed by machine and deliveries | `Manufacturing` |
| 5 | Deliveries | `delivery.created` | `Manufacturing` |
| 6 | Machine | `piece.manufacturing.started` and `piece.manufactured`, per piece | `Manufacturing` |
| 7 | Orders | `order.manufactured` | `Manufacturing` |
| 8 | Deliveries | `delivery.ready` | `ReadyForDelivery` |
| 9 | User | `PATCH /deliveries/delivery/{id}/status` (`Sent`, then `Delivered`) | `ReadyForDelivery` |
| 10 | Deliveries | `delivery.delivered` | `Delivered` |

The user creates the order with `POST /order` and their JWT. Orders first queries
`GET /clients/{id}` at the healthy `client-service` instance discovered through
Consul. It forwards the original token over HTTPS and uses the returned address
when none is supplied. A missing client returns 404; an unavailable query returns
503 before saving the order. Orders then records `PendingPayment` and starts the
explicit Saga: payment reservation and provisional delivery commands are sent
through RabbitMQ, followed by confirmation or compensation.


On approval, orders publishes `order.paid` with the address and piece ids, and the order moves to `Manufacturing`. Two services react to that same fact independently: machine persists the work, and deliveries creates a pending delivery and publishes `delivery.created`. The machine builds the pieces one at a time and publishes `piece.manufacturing.started` and `piece.manufactured` for each one.

When every piece is built, orders publishes `order.manufactured`; deliveries marks the delivery `Ready` and publishes `delivery.ready`, and the order becomes `ReadyForDelivery`. The delivery then advances by hand with `PATCH /deliveries/delivery/{id}/status` (`Sent`, then `Delivered`). On `Delivered`, deliveries publishes `delivery.delivered` and the order ends in `Delivered`. Facts may arrive out of order (for example `order.manufactured` before `order.paid` in deliveries), and the services still end in a single, consistent state.

## CQRS and Consul

Query handlers are in `client_queries.py`, `order_queries.py` and `payment_queries.py`.
They return read models without committing changes. In particular, GET balance does
not create an account. Commands keep their existing POST/DELETE routes, transactions,
Saga and outbox. This is basic CQRS with the existing per-service SQLite databases.

`my_chassis.discovery.query_service` resolves `client-service` through Consul's healthy
instance API and calls the returned IP/port directly over verified HTTPS. The SNI
name is the registered service name. It forwards the requesting user's bearer token;
the client query API enforces the same administrator/owner permissions. Service
addresses are never configured in orders/payments, and internal queries do not pass
through HAProxy. See `vendor/README.md` for manual checks, which have not been run.

## Reliability

The goal is that no fact is lost and no order is charged twice. Creating an order or depositing balance requires the client query service; in-flight Saga operations remain asynchronous. Five mechanisms, most of them in the chassis's `EventBus`, achieve this.

| Mechanism | How it works |
| --- | --- |
| Transactional outbox | A service records the fact in its own `event_outbox` table in the same transaction as the state change. A background loop publishes pending rows and marks them published only after RabbitMQ confirms; outages and restarts are retried automatically. |
| Inbox deduplication | Every received `event_id` is stored in `event_inbox` in the same transaction as its effects. A redelivered or duplicated fact is acknowledged and ignored, so effects happen only once. |
| Durable, predefined queues | The five queues exist from RabbitMQ's boot, and messages are persistent. A service that is down catches up from its queue when it restarts. |
| Persisted keys and asynchronous facts | JWT consumers keep their public key and ongoing Saga facts use durable queues. New order/deposit validation requires a healthy client service discovered through Consul. |
| Idempotent business rules | Payments stores one result per order and never charges twice, including after a later deposit on a rejected order. Deliveries creates one delivery per order and never moves it backwards, even if `order.manufactured` arrives before `order.paid`. Machine keeps its queued and interrupted work in `machine.db`. |

The outbox gives at-least-once delivery: a crash between RabbitMQ's confirmation and marking the row as published can send a fact twice, which the receiving inbox then discards. APIs therefore show eventual progress: a `202` from `POST /order` means the order is accepted for processing, not that payment has finished. Pending outbox publications are retried in the background.

## Deployment and testing

From the repository root:

```bash
cp dot_env_example .env
docker compose up -d --build
curl https://localhost:8080/health
```

On Windows PowerShell, use `Copy-Item dot_env_example .env` instead of `cp`. The `.env` file is required: deliveries reads its database URL from it. RabbitMQ (`rabbitmq:4-management`) and MongoDB have healthchecks, and the services that use them wait until they are healthy.

| Access | URL |
| --- | --- |
| API Gateway over HTTP | `https://localhost:8080/api/...` |
| API Gateway over HTTPS (local development certificate) | `https://localhost:8443/api/...` |
| HAProxy statistics | `https://localhost:8404` (admin / admin) |
| Application logs (admin JWT) | `https://localhost:8080/api/logs` |
| Log aggregator Swagger (local) | `https://localhost:13007/docs` |
| RabbitMQ console | `https://localhost:15672` (manufacturing / manufacturing) |
| Swagger for each service | `:13001/docs`, `:13002/docs`, `:13004/docs`, `:13005/client`, `:13006/payment` |

The gateway routes `/api/orders`, `/api/pieces`, `/api/auth`, `/api/clients`, `/api/payments`, `/api/machine` and `/api/deliveries` to the matching service, keeps the legacy `/api/order` and `/api/delivery` routes, and returns 404 for anything else. HAProxy accepts HTTPS and connects to the APIs over HTTPS using the local certificate set in `infrastructure/tls/generated/`. Consul Template discovers healthy backends through Consul HTTPS and reloads HAProxy when registrations change. The audit trail can be inspected with:

```bash
docker compose exec -T mongodb mongosh --quiet --eval 'db.getSiblingDB("manufacturing_audit").business_events.find({}, {event_type:1,producer:1,received_at:1}).sort({received_at:-1}).limit(20).toArray()'
```

**Unit tests.** Run each in its own process, with the chassis wheel and the service's requirements installed:

```bash
python tests/test_domain_events.py --service order
python tests/test_domain_events.py --service payment
python tests/test_domain_events.py --service delivery
python tests/test_domain_events.py --service machine
python tests/test_refresh_tokens.py
python tests/test_event_logger.py
```

**Integration tests** (with the stack running; they use the gateway at `API_BASE_URL`, `https://localhost:8080` by default). The recovery and audit tests stop containers on purpose and restore them in `finally` blocks:

```bash
python tests/test_auth_integration.py
python tests/test_workflow_integration.py
python tests/test_event_recovery_integration.py
python tests/test_event_audit_integration.py
```

**Manual testing.** With Bruno, select the `Gateway` environment, set `authUsername` and `authPassword`, and run `Auth / Request token`; it stores `accessToken` and `refreshToken` for the other requests. With any other client, get a token with `POST /api/auth/token` (HTTP Basic, `admin` / `admin123`) and send it as `Authorization: Bearer <token>`.

# Task contributions

| Task | Xian | Unai | Unax | Alejandro | Javi |
| --- | --- | --- | --- | --- | --- |
| Monolith split into microservices | 2 | 2 | 2 | 2 | 2 |
| API Gateway (HAProxy, HTTPS) | 1 | 5 | 1 | 2 | 1 |
| Client service and JWT authentication | 1 | 4 | 3 | 1 | 1 |
| Refresh tokens and service tokens | 2 | 1 | 2 | 5 | 0 |
| JWT in machine and delivery | 0 | 3 | 5 | 1 | 1 |
| RabbitMQ: payment flow | 1 | 4 | 1 | 3 | 1 |
| RabbitMQ: machine integration | 1 | 2 | 4 | 1 | 2 |
| Event choreography (outbox / inbox) | 1 | 2 | 1 | 3 | 3 |
| Microservice chassis | 6 | 1 | 1 | 1 | 1 |
| Event logger and MongoDB audit | 0 | 1 | 6 | 2 | 1 |
| Docker Compose and deployment fixes | 2 | 3 | 2 | 1 | 2 |
| Tests (unit and integration) | 4 | 3 | 3 | 0 | 0 |
| Documentation and presentation | 0 | 0 | 0 | 5 | 5 |
