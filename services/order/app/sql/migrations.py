"""Add workflow fields to existing SQLite databases without removing orders."""
from sqlalchemy import inspect, text


def upgrade(connection):
    client_columns = {column["name"] for column in inspect(connection).get_columns("known_client")}
    for name, definition in {"address": "VARCHAR(500)", "postal_code": "VARCHAR(5)"}.items():
        if name not in client_columns:
            connection.execute(text(f"ALTER TABLE known_client ADD COLUMN {name} {definition}"))
    columns = {column["name"] for column in inspect(connection).get_columns("manufacturing_order")}
    additions = {
        "client_id": "INTEGER",
        "address": "VARCHAR(500)",
        "unit_price": "INTEGER NOT NULL DEFAULT 10",
        "total_price": "INTEGER NOT NULL DEFAULT 0",
        "payment_completed": "BOOLEAN NOT NULL DEFAULT 0",
        "order_created_published": "BOOLEAN NOT NULL DEFAULT 0",
        "manufacturing_requested": "BOOLEAN NOT NULL DEFAULT 0",
        "delivery_id": "INTEGER",
        "integration_error": "TEXT",
        "delivery_ready_published": "BOOLEAN NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE manufacturing_order ADD COLUMN {name} {definition}"))
