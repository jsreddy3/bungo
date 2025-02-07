# src/database.py

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from src.services.llm_service import LLMService
import os

# Simple environment-based switch
DB_TYPE = os.getenv("DB_TYPE", "sqlite")  # Default to SQLite

if DB_TYPE == "sqlite":
    SQLALCHEMY_DATABASE_URL = "sqlite:///./game.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL setup
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/game_db")
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

_llm_service = None

def get_llm_service():
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()