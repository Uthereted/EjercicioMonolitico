"""Preserve existing clients while adding their delivery address."""
from sqlalchemy import inspect, text


def upgrade(connection):
    columns = {column["name"] for column in inspect(connection).get_columns("clients")}
    for name, definition in {"address": "VARCHAR(500)", "postal_code": "VARCHAR(5)"}.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE clients ADD COLUMN {name} {definition}"))
