# src/models/database_models.py
from sqlalchemy import Column, ForeignKey, String, Float, Boolean, DateTime, Integer
from sqlalchemy.orm import relationship
from src.database import Base
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import TypeDecorator, CHAR
import uuid

class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses
    CHAR(32), storing as stringified hex values.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(UUID())
        else:
            return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                return "%.32x" % uuid.UUID(value).int
            else:
                return "%.32x" % value.int

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                value = uuid.UUID(value)
            return value

class DBSession(Base):
    __tablename__ = "sessions"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    start_time = Column(DateTime(timezone=True), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    entry_fee = Column(Float, nullable=False)
    total_pot = Column(Float, default=0)
    status = Column(String, nullable=False)
    attempts = relationship("DBAttempt", back_populates="session")

class DBAttempt(Base):
    __tablename__ = "attempts"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    session_id = Column(GUID(), ForeignKey("sessions.id"))
    user_id = Column(GUID(), nullable=False)
    is_winner = Column(Boolean, default=False)
    messages_remaining = Column(Integer, default=5)
    session = relationship("DBSession", back_populates="attempts")
    messages = relationship("DBMessage", back_populates="attempt")

class DBMessage(Base):
    __tablename__ = "messages"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(GUID(), ForeignKey("attempts.id"))
    content = Column(String, nullable=False)
    ai_response = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)
    attempt = relationship("DBAttempt", back_populates="messages")