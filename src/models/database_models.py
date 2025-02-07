# src/models/database_models.py
from sqlalchemy import Column, ForeignKey, String, Float, Boolean, DateTime, Integer
from sqlalchemy.orm import relationship
from src.database import Base
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import TypeDecorator, CHAR
import uuid
from uuid import uuid4
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")

class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=UTC)
        return value

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
    start_time = Column(UTCDateTime, nullable=False)
    end_time = Column(UTCDateTime, nullable=False)
    entry_fee = Column(Float, nullable=False)
    total_pot = Column(Float, default=0)
    status = Column(String, nullable=False)

    attempts = relationship("DBAttempt", back_populates="session")

class DBAttempt(Base):
    __tablename__ = "attempts"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id = Column(GUID(), ForeignKey("users.id"))
    earnings = Column(Float, default=0.0)
    session_id = Column(GUID(), ForeignKey("sessions.id"))
    score = Column(Float, default=0.0)  # Add this
    messages_remaining = Column(Integer, default=3)

    session = relationship("DBSession", back_populates="attempts")
    messages = relationship("DBMessage", back_populates="attempt")
    user = relationship("DBUser", back_populates="attempts")

class DBMessage(Base):
    __tablename__ = "messages"
    
    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(GUID(), ForeignKey("attempts.id"))
    content = Column(String, nullable=False)
    ai_response = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False)

    attempt = relationship("DBAttempt", back_populates="messages")

class DBUser(Base):
    __tablename__ = "users"
    
    id = Column(GUID(), primary_key=True, default=uuid4)
    wldd_id = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_active = Column(DateTime(timezone=True), nullable=False)
    
    # Relationships
    attempts = relationship("DBAttempt", back_populates="user")
    
    def get_stats(self):
        return {
            "total_games": len(self.attempts),
            "total_wins": len([a for a in self.attempts if a.score > 7.0]),
            "total_earnings": sum(a.earnings for a in self.attempts if a.earnings)
        }