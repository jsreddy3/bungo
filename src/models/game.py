# src/models/game.py

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict
from datetime import datetime
from uuid import UUID, uuid4
from enum import Enum
from zoneinfo import ZoneInfo
UTC = ZoneInfo("UTC")

class SessionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"

class Message(BaseModel):
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class GameAttempt(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    user_id: UUID
    messages: List[Message] = Field(default_factory=list)
    is_winner: bool = False
    messages_remaining: int = Field(default=5)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    @field_validator("messages")
    @classmethod
    def validate_message_count(cls, v: List[Message]):
        if len(v) > 5:
            raise ValueError("Cannot exceed 5 messages")
        return v

class GameSession(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    start_time: datetime
    end_time: datetime
    entry_fee: float
    total_pot: float = 0
    status: SessionStatus = SessionStatus.PENDING
    winning_attempts: List[UUID] = Field(default_factory=list)
    attempts: List[UUID] = Field(default_factory=list)

    def add_attempt(self, attempt_id: UUID):
        self.attempts.append(attempt_id)
        self.total_pot += self.entry_fee

    def complete_session(self, winning_attempt_ids: List[UUID]):
        self.winning_attempts = winning_attempt_ids
        self.status = SessionStatus.COMPLETED

class User(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    wldd_id: str = Field(..., pattern="^WLDD-[0-9A-Z]{8}$")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = Field(default_factory=lambda: datetime.now(UTC))
    total_games_played: int = Field(default=0, ge=0)
    total_games_won: int = Field(default=0, ge=0)
    total_winnings: float = Field(default=0, ge=0)
    is_active: bool = Field(default=True)
    game_attempts: List[UUID] = Field(default_factory=list)
    preferences: Dict[str, str] = Field(default_factory=dict)

    def add_attempt(self, attempt_id: UUID):
        self.game_attempts.append(attempt_id)
        self.total_games_played += 1
        self.last_active = datetime.now(UTC)

    def add_win(self, winnings: float):
        self.total_games_won += 1
        self.total_winnings += winnings
        self.last_active = datetime.now(UTC)

    def get_stats(self) -> Dict[str, float]:
        return {
            "total_games": self.total_games_played,
            "total_wins": self.total_games_won,
            "win_rate": self.total_games_won / self.total_games_played if self.total_games_played > 0 else 0,
            "total_winnings": self.total_winnings,
            "average_winnings": self.total_winnings / self.total_games_won if self.total_games_won > 0 else 0
        }