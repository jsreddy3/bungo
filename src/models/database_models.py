# src/models/database_models.py
from sqlalchemy import Column, ForeignKey, String, Float, Boolean, DateTime, Integer, Index
from sqlalchemy.orm import relationship
from src.database import Base
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.types import TypeDecorator, CHAR
import uuid
from uuid import uuid4
from zoneinfo import ZoneInfo
from datetime import datetime

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

    id = Column(GUID(), primary_key=True, default=uuid4)
    wldd_id = Column(String, ForeignKey("users.wldd_id"))
    session_id = Column(GUID(), ForeignKey("sessions.id"))
    earnings = Column(Float, default=0.0)
    score = Column(Float, default=0.0)
    messages_remaining = Column(Integer, default=5)
    created_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC))

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
    
    wldd_id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    last_active = Column(DateTime(timezone=True), nullable=False)
    
    # Relationships
    attempts = relationship("DBAttempt", back_populates="user")
    payments = relationship("DBPayment", back_populates="user")
    
    def get_stats(self):
        return {
            "total_games": len(self.attempts),
            "total_wins": len([a for a in self.attempts if a.score > 7.0]),
            "total_earnings": sum(a.earnings for a in self.attempts if a.earnings)
        }

class DBVerification(Base):
    __tablename__ = "verifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    nullifier_hash = Column(String, nullable=False)
    merkle_root = Column(String, nullable=False)
    action = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    
    __table_args__ = (
        Index('idx_verification_nullifier_date', 'nullifier_hash', 'created_at'),
    )

class DBPayment(Base):
    __tablename__ = "payments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    reference = Column(String, unique=True, nullable=False)
    status = Column(String, default="pending")  # pending, confirmed, failed
    transaction_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    wldd_id = Column(String, ForeignKey("users.wldd_id"))
    
    user = relationship("DBUser", back_populates="payments")
    
    __table_args__ = (
        Index('idx_payment_reference', 'reference'),  # For fast lookups by reference
        Index('idx_payment_user', 'wldd_id'),        # For fast user payment history
    )