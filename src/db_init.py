# src/db_init.py
# ONLY RUN ONCE! 

from src.database import engine, Base
from src.models.database_models import DBSession, DBAttempt, DBMessage

def init_db():
    Base.metadata.create_all(bind=engine)

if __name__ == "__main__":
    init_db()