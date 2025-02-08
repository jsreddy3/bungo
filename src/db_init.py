# src/db_init.py
from src.database import engine, Base
from src.models.database_models import DBSession, DBAttempt, DBMessage, DBUser  # Add DBUser

def init_db():
    print("Initializing database...")
    Base.metadata.create_all(bind=engine)  # This will only create tables that don't exist
    print("Database initialization complete")

if __name__ == "__main__":
    init_db()