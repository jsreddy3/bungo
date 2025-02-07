# src/db_init.py
from src.database import engine, Base
from src.models.database_models import DBSession, DBAttempt, DBMessage, DBUser  # Add DBUser

def init_db():
    print("Creating database tables...")
    Base.metadata.drop_all(bind=engine)  # Drop all existing tables
    Base.metadata.create_all(bind=engine)
    print("Database tables created successfully")

if __name__ == "__main__":
    init_db()