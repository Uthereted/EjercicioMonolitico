from app.sql.database import SessionLocal

async def get_db():
    db = SessionLocal()
    try:
        yield db
        await db.commit()
    except:
        await db.rollback()
        raise
    finally:
        await db.close()