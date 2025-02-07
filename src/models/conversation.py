from pydantic import BaseModel, Field, field_validator
from typing import List, Optional
from datetime import datetime
from uuid import UUID, uuid4

class Message(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    role: str = Field(..., pattern="^(user|assistant)$")
    tokens: int = Field(ge=0)

class Conversation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    user: Optional[User] = None  # Back-reference to user
    messages: List[Message] = Field(default_factory=list)
    total_cost: float = Field(ge=0, default=0)
    message_count: int = Field(ge=0, default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    title: Optional[str] = None
    status: str = Field(default="active", pattern="^(active|archived|deleted)$")
    model_name: str = Field(default="gpt-3.5-turbo")
    
    @field_validator("message_count")
    @classmethod
    def validate_message_count(cls, v: int, info):
        if len(info.data.get("messages", [])) != v:
            raise ValueError("message_count must match the length of messages")
        return v

    @field_validator("updated_at")
    @classmethod
    def validate_updated_at(cls, v: datetime, info):
        if v < info.data.get("created_at", datetime.min):
            raise ValueError("updated_at cannot be earlier than created_at")
        return v