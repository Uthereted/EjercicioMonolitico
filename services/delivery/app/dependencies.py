from my_chassis.database import create_db_dependency
from .sql.database import SessionLocal


get_db = create_db_dependency(SessionLocal)
