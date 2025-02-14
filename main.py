# main.py

import uvicorn
from src.database import Base, engine
from src.routes.api import app
from src.config.logging_config import configure_logging

# Configure logging first
logger = configure_logging()

def init_db():
    logger.info("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialized successfully")

def main():
    # Initialize database tables
    init_db()
    
    logger.info("Starting FastAPI application...")
    # Run the FastAPI application
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True,  # Enable auto-reload during development
        log_config=None,  # Disable uvicorn's default logging
    )

if __name__ == "__main__":
    logger.info("Starting application in __main__")
    uvicorn.run(
        "src.routes.api:app", 
        host="0.0.0.0", 
        port=8000, 
        reload=True,
        log_config=None,  # Disable uvicorn's default logging
    )