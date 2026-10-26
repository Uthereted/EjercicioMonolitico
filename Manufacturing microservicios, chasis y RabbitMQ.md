# Manufacturing: Microservices, Chassis and RabbitMQ

## Summary

The manufacturing monolith has been split into five FastAPI microservices (clients, orders, payments, machine and deliveries), each with its own database, behind an HAProxy API Gateway reachable over HTTP and HTTPS. Integrations between services that do not need an immediate response have moved to asynchronous events on RabbitMQ. Shared infrastructure (logging, health check, database sessions and event publishing) has been extracted into a chassis library, `microservice_chassis`, and sensitive operations are protected with JWT tokens issued by the client service.

This document describes the resulting architecture, authentication, the chassis, the event contracts, the reliability guarantees, and how to deploy and test the system.

## Architecture overview

&#91;embedded content: architecture · 5 microservices, gateway and event bus\]

All external requests enter through HAProxy, which routes them by path prefix. Between services, communication runs on RabbitMQ events except for three REST calls that need an immediate reply: orders validates the client in the client service (`GET /clients/{id}`), orders creates the delivery in the delivery service (`POST /deliveries/delivery`), and orders and payments download the public key used to verify tokens (`GET /auth/public-key`). Each service has its own SQLite database on a Docker volume.

## Microservices

Each service lives in `services/<name>/`, has its own Dockerfile and is the sole owner of its data: no service reads another service's database.

**Clients** (`client_service`, port 13005) handles client registration and lookup and is also the authentication service: it stores clients, users and roles in `client.db`, issues JWT tokens and publishes `client.cert.updated` whenever it generates a new signing key.

**Orders** (`order-service`, port 13001) orchestrates the whole order flow and owns the state of every piece. It stores orders and pieces in `orders.db`. It publishes `order.created`, `machine.pieces.requested` and `order.delivery.ready`, and consumes payment, piece and delivery events.

**Payments** (`paymentservice`, port 13006) manages accounts, deposits and order charges, with its data in `payment.db`. It consumes `order.created` and replies with `payment.approved` or `payment.rejected`.

**Machine** (`machineservice`, port 13002) simulates manufacturing pieces one at a time. It has no database: it keeps its queue in memory. It consumes `machine.pieces.requested` and publishes `piece.status.update`.

**Deliveries** (`delivery`, port 13004) manages the delivery lifecycle and stores its data in `delivery.db`. It consumes `order.delivery.ready` to mark the delivery as `Ready` and publishes `delivery.status.update` when a delivery is completed.

Orders is the orchestrator: it decides when to charge, when to manufacture and when to deliver. Machine keeps no state of its own; if it restarts, it recovers pending pieces by querying orders (`GET /piece/by-status/{status}`).

Calls that need an immediate reply remain REST: orders validates the client with `GET /clients/{id}` when creating the order and creates the delivery with `POST /deliveries/delivery`. Marking the delivery as `Ready` is no longer a REST call: it now goes through the `order.delivery.ready` event.

## Authentication

The client service also acts as the authentication service. It issues JWT tokens signed with RS256 (a 2048-bit RSA private key) that expire after one hour. Orders and payments verify them with the public key, without calling the client service on every request.

| Client service endpoint | Gateway route | Purpose |
| --- | --- | --- |
| `POST /auth/register` | `/api/auth/register` | Registers a user. Public registration only allows the `user` role. |
| `GET /auth/token` | `/api/auth/token` | Login with HTTP Basic (username and password). Returns `access_token`. |
| `GET /auth/public-key` | `/api/auth/public-key` | PEM public key used to verify tokens. |

The token contains `sub`, `username`, `email`, `roles` and `client_id`, plus `iss=manufacturing-client` and `aud=manufacturing-services`. The `client_id` is found by looking up the client with the same email as the user, so the user and client tables are independent. Passwords are stored with bcrypt. On startup the service creates the `admin` and `user` roles and an `admin` / `admin123` user for testing.

**Protected routes.** You must send `Authorization: Bearer <access_token>` to create and list orders (`POST` and `GET /order`) and to deposit, charge or check the balance in payments. A regular user can only act on their own `client_id`; the `admin` role can act on any. Other routes do not require a token: this integration does not protect every API.

**Key rotation.** The client service generates its signing key in memory at startup and publishes `client.cert.updated` on RabbitMQ. Orders and payments listen for that event on their own exclusive queue, separate from `order.events`, and download the public key again. They also re-download it when they receive a token with a signature they do not recognize. Because the key is not persisted, restarting the client service invalidates all previous tokens and users must log in again.

This authentication is intended for development: registration does not verify email ownership, and JWT verification is duplicated in `auth_verifier.py` in orders and payments instead of living in the chassis.

## Microservice chassis

The chassis is a Python library that lives in its own repository, [Uthereted/chasis\_homework](https://github.com/Uthereted/chasis_homework), separate from the project. It is distributed as the `microservice_chassis` 0.4.0 package and imported in Python as `my_chassis`. It gathers the infrastructure that was repeated across services so that each service only contains its business logic. There is no mandatory base class: each service imports only what it needs.

| Module and function | What it does | Used by |
| --- | --- | --- |
| `my_chassis.configure_logging(name)` | Configures console logging with a common format that includes `[service]`. | Orders and payments |
| `my_chassis.create_health_router(name)` | Adds `GET /health`, which returns `{"status":"ok","service":"<name>"}`. | Orders and payments |
| `my_chassis.database.create_db_dependency(session_factory)` | Creates the FastAPI `get_db` dependency, which opens one session per request and closes it even if the route fails. | Clients, deliveries, orders and payments |
| `my_chassis.messaging.publish_json_event(...)` | Publishes a persistent JSON event with broker confirmation. Before publishing it declares the exchange and the destination queue and creates their bindings. | Orders, payments and deliveries |
| `my_chassis.messaging.create_json_message(...)` | Builds the AMQP message: JSON, persistent, with `type` and `message_id`. | The `order.created` publisher in orders |

`publish_json_event` does not retry or commit to the database: if it fails, it raises the exception and the service keeps its event pending to retry later (the outbox pattern described under Reliability). Models, business rules and JWT verification are not part of the chassis either; they stay in each service. Machine is the only service that does not use the chassis.

Usage in a service:

```python
from my_chassis import configure_logging, create_health_router
from my_chassis.database import create_db_dependency
from my_chassis.messaging import publish_json_event

configure_logging("order-service")
get_db = create_db_dependency(SessionLocal)
app.include_router(create_health_router("order-service"))

await publish_json_event(RABBITMQ_URL, EXCHANGE, "machine.pieces.requested",
                         "machine.events", payload, message_id)
```

**Installation.** Each service installs it from GitHub in its `requirements.txt`. Services that publish events request the `[messaging]` extra, which adds `aio-pika`:

```text
microservice_chassis[messaging] @ git+https://github.com/Uthereted/chasis_homework.git@main
```

The client service uses the variant without the extra. The Dockerfiles install `git` and `ca-certificates` before `pip install`. Cloning the project is enough: no local copy of the chassis is needed.

**Versions.** The dependency points to the chassis `main` branch. Docker reuses the `pip` layer as long as `requirements.txt` does not change, so picking up chassis changes requires a rebuild without cache:

```bash
docker compose build --no-cache order-service paymentservice delivery client_service
docker compose up -d order-service paymentservice delivery client_service
```

For the final submission, pin the dependency to a chassis commit or tag so everyone installs exactly the same version.

**Health check scope.** `/health` only shows that the process answers HTTP; it does not check the database or RabbitMQ. It is available at `http://localhost:13001/health` and `http://localhost:13006/health`. The gateway's `/health` is independent.

Natural next steps would be to move more into the chassis: the JWT verification duplicated in orders and payments, and the consumer loop with reconnection, which is repeated in every consumer.

## RabbitMQ: queues and event contracts

All events go through a single durable **topic** exchange, `manufacturing.events`. Each consuming service has its own durable queue and binds it to the routing keys it cares about. Services connect with `aio-pika` using `RABBITMQ_URL=amqp://manufacturing:manufacturing@rabbitmq:5672/`.

| Routing key | Published by | Queue | Consumed by | JSON body |
| --- | --- | --- | --- | --- |
| `order.created` | Orders | `payment.events` | Payments | `{order_id, client_id, amount}` |
| `payment.approved` | Payments | `order.payment.events` | Orders | `{order_id, client_id, amount, accepted: true, reason}` |
| `payment.rejected` | Payments | `order.payment.events` | Orders | `{order_id, client_id, amount, accepted: false, reason}` |
| `machine.pieces.requested` | Orders | `machine.events` | Machine | `{order_id, piece_ids: [int]}` |
| `piece.status.update` | Machine | `order.events` | Orders | `{piece_id, status: "Manufacturing" \| "Manufactured"}` |
| `order.delivery.ready` | Orders | `delivery.events` | Deliveries | `{order_id, delivery_id, status: "Ready"}` |
| `delivery.status.update` | Deliveries | `order.events` | Orders | `{delivery_id, order_id, status: "Delivered"}` |
| `client.cert.updated` | Clients | One exclusive queue per instance | Orders and payments | `{}` (just a notification) |

All messages are published as persistent (`delivery_mode=PERSISTENT`), with `content_type=application/json`, `type` equal to the routing key, and a deterministic `message_id` (for example `piece.status.update:42:Manufactured`) that makes duplicates detectable.

There are five durable queues: `payment.events`, `order.payment.events`, `machine.events`, `order.events` and `delivery.events`. `order.events` carries two event types and has a single consumer in orders (`consume_order_events`), which tells them apart by routing key. `client.cert.updated` is the exception: it is not published as persistent, and each orders and payments instance receives it on a temporary exclusive queue that is deleted on disconnect, because a stale key notification is useless.

Every message is validated with a Pydantic model before processing. The consumer then decides:

- **ack** if it was processed successfully.
- **reject without requeue** if the message is invalid or contradictory: a validation error, an unknown routing key, or data that does not match (for example, a delivery that does not belong to the order). Retrying would never fix it.
- **nack with requeue**, after waiting 5 s, for transient errors: the database or another service being down, or a `piece.status.update` that arrives too early (a `Manufactured` for a piece that orders has not yet seen as `Manufacturing`).

## Order flow

&#91;embedded content: order flow · 8 steps, 6 events\]

The client creates the order with `POST /order`, sending their JWT token. The order starts in `PendingPayment` and orders publishes `order.created`. Payments charges the client and replies with `payment.approved` (the order moves to `Accepted`) or `payment.rejected` (it moves to `Rejected` and nothing is manufactured). Once payment is accepted, orders publishes `machine.pieces.requested` with the piece ids and creates the delivery over REST.

The machine manufactures the pieces one at a time and publishes two `piece.status.update` events per piece: `Manufacturing` when it starts and `Manufactured` when it finishes. When all pieces are done, orders publishes `order.delivery.ready`, deliveries marks the delivery as `Ready`, and the order moves to `ReadyForDelivery`. The delivery then advances manually with `PATCH /api/deliveries/{id}/status` (`Sent`, `Delivered`). When it reaches `Delivered`, deliveries publishes `delivery.status.update` and the order ends in `Delivered`.

## Reliability

The goal is that no order is lost or charged twice even if RabbitMQ or any service goes down. Five mechanisms achieve this.

| Mechanism | Where | How it works |
| --- | --- | --- |
| Outbox with database flags | Orders (`order_created_published`, `manufacturing_requested`, `delivery_ready_published`), deliveries (`order_notified`) | The event is published first and the flag is set only afterwards. If RabbitMQ does not respond, the flag stays `False` and a background loop retries (`publish_pending_events`, `retry_pending_orders`, `retry_notifications`). |
| Broker confirmation | All publishers | Channels with `publisher_confirms=True` and `mandatory=True` (in the chassis's `publish_json_event`): a publication only counts as done when RabbitMQ confirms it has stored it in a queue. |
| Durable queues declared by the publisher | All | The publisher also declares and binds the destination queue, so the message is kept even if the consumer has never started. |
| Idempotency | Payments, orders, machine, deliveries | Payments stores the result per order in `OrderPaymentResult` and never charges twice. Orders ignores states it already has: a `Manufactured` piece never goes back. Machine discards repeated or already finished pieces (`pending_ids`, `completed_ids`). Deliveries never moves a delivery's status backwards. |
| Reconnection and retry | All consumers | If the connection drops, the loop waits 5 s and reconnects. Machine retries each piece event until it is confirmed, showing the `PendingNotification` status meanwhile. |

One known limitation: machine acknowledges (`ack`) the request as soon as it puts the pieces in its in-memory queue, not when it finishes manufacturing them. If the container dies midway, those pieces are recovered over REST from orders on restart (`recovery_coroutine`). The alternative would be one message per piece with `prefetch_count=1` and an `ack` after publishing `Manufactured`, at the cost of no longer being able to list the queue in `GET /machine/status`.

## Deployment and testing

Everything starts from the repository root with `docker compose up -d --build`. RabbitMQ (`rabbitmq:4-management`) has a healthcheck, and the services that use it wait until it is healthy (`depends_on: condition: service_healthy`).

| Access | URL |
| --- | --- |
| API Gateway (HAProxy) over HTTP | `http://localhost:8080/api/...` |
| API Gateway over HTTPS (local development certificate) | `https://localhost:8443/api/...` |
| HAProxy statistics | `http://localhost:8404` (admin / admin) |
| RabbitMQ console | `http://localhost:15672` (manufacturing / manufacturing) |
| Swagger for each service | `:13001/docs`, `:13002/docs`, `:13004/docs`, `:13005/client`, `:13006/payment` |
| Orders and payments health | `http://localhost:13001/health`, `http://localhost:13006/health` |

The gateway routes `/api/orders`, `/api/pieces`, `/api/auth`, `/api/clients`, `/api/payments`, `/api/machine` and `/api/deliveries` to the matching service, keeps the legacy `/api/order` and `/api/delivery` routes for the Bruno collection, and returns 404 for anything else. HTTPS terminates at HAProxy with `haproxy/gateway.pem`; inside the Docker network, traffic to the services remains HTTP.

**Unit tests** (from the repository root, without Docker, with the services' dependencies installed):

```bash
python tests/test_order_messaging.py
python tests/test_payment_messaging.py
python tests/test_delivery_messaging.py
python tests/test_machine_messaging.py
```

`tests/test_auth_verifier.py` tests JWT verification and runs in the orders or payments environment.

**Integration tests** (with everything running in Docker; they use the gateway at `API_BASE_URL`, `http://localhost:8080` by default):

```bash
python tests/test_auth_integration.py
python tests/test_workflow_integration.py
```

The second one logs in as `admin` (configurable with `TEST_AUTH_USERNAME` and `TEST_AUTH_PASSWORD`), creates a client, deposits 100, orders two pieces, completes the delivery and checks that the final balance is 80.

**Manual testing.** To test with Postman or Bruno: get a token with `GET /api/auth/token` (HTTP Basic, `admin` / `admin123`) and send it as `Authorization: Bearer <token>` when depositing and when creating the order. The remaining steps (checking the order and its pieces, completing the delivery) do not need a token.
