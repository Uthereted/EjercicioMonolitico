"""Add delivery workflow fields without removing existing deliveries."""
from sqlalchemy import inspect, text
from . import models


def upgrade(connection):
    columns = {column["name"] for column in inspect(connection).get_columns("delivery")}
    if "order_notified" not in columns:
        connection.execute(text("ALTER TABLE delivery ADD COLUMN order_notified BOOLEAN NOT NULL DEFAULT 0"))
    if "workflow_managed" not in columns:
        connection.execute(text("ALTER TABLE delivery ADD COLUMN workflow_managed BOOLEAN NOT NULL DEFAULT 0"))
    for index in models.Delivery.__table__.indexes:
        index.create(connection, checkfirst=True)
