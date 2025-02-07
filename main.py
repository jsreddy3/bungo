# main.py

import uvicorn
from src.database import Base, engine
from src.routes.api import app

def init_db():
    Base.metadata.create_all(bind=engine)

def main():
    # Initialize database tables
    init_db()
    
    # Run the FastAPI application
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True  # Enable auto-reload during development
    )

if __name__ == "__main__":
    uvicorn.run("src.routes.api:app", host="0.0.0.0", port=8000, reload=True)