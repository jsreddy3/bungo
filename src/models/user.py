from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict
from datetime import datetime
from uuid import UUID, uuid4

class User(BaseModel):
   id: UUID = Field(default_factory=uuid4)
   wldd_id: str = Field(..., pattern="^WLDD-[0-9A-Z]{8}$")
   email: str
   name: str
   created_at: datetime = Field(default_factory=datetime.utcnow)
   last_active: datetime = Field(default_factory=datetime.utcnow)
   total_messages_sent: int = Field(default=0, ge=0)
   total_spend: float = Field(default=0, ge=0)
   is_active: bool = Field(default=True)
   conversations: List["Conversation"] = Field(default_factory=list)
   preferences: Dict[str, str] = Field(default_factory=dict)
   
   @field_validator("email")
   @classmethod
   def validate_email(cls, v: str):
       if "@" not in v or "." not in v:
           raise ValueError("Invalid email format")
       return v.lower()
   
   @field_validator("last_active")
   @classmethod
   def validate_last_active(cls, v: datetime, info):
       if v < info.data.get("created_at", datetime.min):
           raise ValueError("last_active cannot be earlier than created_at")
       return v

   def add_conversation(self, conversation: "Conversation"):
       self.conversations.append(conversation)
       self.total_messages_sent += conversation.message_count
       self.total_spend += conversation.total_cost
       self.last_active = datetime.utcnow()

   def get_recent_conversations(self, limit: int = 10) -> List["Conversation"]:
       return sorted(
           self.conversations,
           key=lambda x: x.updated_at,
           reverse=True
       )[:limit]
   
   def get_total_stats(self) -> Dict[str, float]:
       return {
           "total_messages": self.total_messages_sent,
           "total_spend": self.total_spend,
           "average_cost_per_message": (
               self.total_spend / self.total_messages_sent 
               if self.total_messages_sent > 0 else 0
           )
       }