"""Add workflow fields to existing SQLite databases without removing orders."""
from sqlalchemy import inspect, text


def upgrade(connection):
    columns = {column["name"] for column in inspect(connection).get_columns("manufacturing_order")}
    additions = {
        "client_id": "INTEGER",
        "address": "VARCHAR(500)",
        "unit_price": "INTEGER NOT NULL DEFAULT 10",
        "total_price": "INTEGER NOT NULL DEFAULT 0",
        "payment_completed": "BOOLEAN NOT NULL DEFAULT 0",
        "manufacturing_requested": "BOOLEAN NOT NULL DEFAULT 0",
        "delivery_id": "INTEGER",
        "integration_error": "TEXT",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE manufacturing_order ADD COLUMN {name} {definition}"))
