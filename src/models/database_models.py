# src/models/database_models.py
from sqlalchemy import Column, ForeignKey, String, Float, Boolean, DateTime, Integer, Index, BigInteger
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
    entry_fee_raw = Column(BigInteger)  # Store fee in smallest unit
    total_pot_raw = Column(BigInteger, default=0)  # Change this from Float
    status = Column(String, nullable=False)
    winning_attempt_id = Column(UUID(as_uuid=True), ForeignKey('attempts.id'), nullable=True)

    attempts = relationship("DBAttempt", 
                          back_populates="session",
                          foreign_keys="[DBAttempt.session_id]")
    winning_attempt = relationship("DBAttempt", 
                                 foreign_keys=[winning_attempt_id])

    @property
    def entry_fee(self):
        """Get entry fee in USDC/WLD units"""
        return round(float(self.entry_fee_raw) * 10**-6, 2) if self.entry_fee_raw is not None else None
        
    @entry_fee.setter
    def entry_fee(self, value):
        """Set entry fee from USDC/WLD units"""
        if value is not None:
            self.entry_fee_raw = int(value * 10**6)
        else:
            self.entry_fee_raw = None

    @property
    def total_pot(self):
        """Get total pot in USDC/WLD units"""
        return round(float(self.total_pot_raw) * 10**-6, 2) if self.total_pot_raw is not None else None
        
    @total_pot.setter
    def total_pot(self, value):
        """Set total pot from USDC/WLD units"""
        if value is not None:
            self.total_pot_raw = int(value * 10**6)
        else:
            self.total_pot_raw = None

class DBAttempt(Base):
    __tablename__ = "attempts"

    id = Column(GUID(), primary_key=True, default=uuid4)
    wldd_id = Column(String, ForeignKey("users.wldd_id"))
    session_id = Column(GUID(), ForeignKey("sessions.id"))
    earnings_raw = Column(BigInteger, default=0)  # Store earnings in smallest unit
    score = Column(Float, default=0.0)
    messages_remaining = Column(Integer, default=5)
    created_at = Column(UTCDateTime, nullable=False, default=lambda: datetime.now(UTC))

    session = relationship("DBSession", 
                         back_populates="attempts",
                         foreign_keys=[session_id])
    messages = relationship("DBMessage", back_populates="attempt")
    user = relationship("DBUser", back_populates="attempts")

    @property
    def earnings(self):
        """Get earnings in USDC/WLD units"""
        return round(float(self.earnings_raw) * 10**-6, 2) if self.earnings_raw is not None else None
        
    @earnings.setter
    def earnings(self, value):
        """Set earnings from USDC/WLD units"""
        if value is not None:
            self.earnings_raw = int(value * 10**6)
        else:
            self.earnings_raw = None

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
    amount_raw = Column(BigInteger)  # Store amount in smallest unit (e.g., 100000 for 0.1)
    
    # Add consumption tracking
    consumed = Column(Boolean, default=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    consumed_by_attempt_id = Column(UUID(as_uuid=True), ForeignKey('attempts.id'), nullable=True)
    
    user = relationship("DBUser", back_populates="payments")
    consumed_by_attempt = relationship("DBAttempt", foreign_keys=[consumed_by_attempt_id])
    
    __table_args__ = (
        Index('idx_payment_reference', 'reference'),
        Index('idx_payment_user', 'wldd_id'),
    )

    @property
    def amount(self):
        """Get amount in USDC/WLD units"""
        return round(float(self.amount_raw) * 10**-6, 2) if self.amount_raw is not None else None
        
    @amount.setter
    def amount(self, value):
        """Set amount from USDC/WLD units"""
        if value is not None:
            self.amount_raw = int(value * 10**6)
        else:
            self.amount_raw = None