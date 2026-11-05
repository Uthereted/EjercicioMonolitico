"""Ensure the legacy charge uniqueness index exists on existing databases."""
from . import models


def upgrade(connection):
    for index in models.Transaction.__table__.indexes:
        index.create(connection, checkfirst=True)
