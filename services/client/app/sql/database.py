"""Service-owned database configured through the shared chassis."""
import os
from my_chassis.database import create_database

SQLALCHEMY_DATABASE_URL = os.getenv("SQLALCHEMY_DATABASE_URL", "sqlite+aiosqlite:///./client.db")
engine, SessionLocal, Base = create_database(SQLALCHEMY_DATABASE_URL, expire_on_commit=True)
