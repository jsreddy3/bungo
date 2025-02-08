# src/database.py

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from src.services.llm_service import LLMService
from functools import lru_cache

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game.db")

# Handle Heroku's postgres:// vs postgresql:// difference
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Create engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=0
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

@lru_cache()
def get_llm_service():
    return LLMService()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()