# src/services/conversation.py
from typing import List, Optional
from datetime import datetime
from src.models.game import Message
from src.services.llm_service import LLMService
from src.models.database_models import DBMessage, DBAttempt
from sqlalchemy.orm import Session
from uuid import UUID
from zoneinfo import ZoneInfo
UTC = ZoneInfo("UTC")

class ConversationManager:
    def __init__(self, llm_service: LLMService, db: Session):
        self.llm_service = llm_service
        self.db = db
        
    async def process_attempt_message(self, attempt_id: UUID, message_content: str) -> DBMessage:
        attempt = self.db.query(DBAttempt).filter(
            DBAttempt.id == attempt_id
        ).first()
        
        if not attempt:
            raise ValueError("Attempt not found")
            
        if attempt.messages_remaining <= 0:
            raise ValueError("No messages remaining")
        
        # Convert DB messages to domain Message objects for LLM service
        history = [
            Message(content=msg.content, timestamp=msg.timestamp)
            for msg in attempt.messages
        ]
        
        response = await self.llm_service.process_message(
            message_content,
            history
        )
        
        new_message = DBMessage(
            attempt_id=attempt_id,
            content=message_content,
            ai_response=response.content,
            timestamp=datetime.now(UTC)
        )
        
        attempt.messages_remaining -= 1
        
        if attempt.messages_remaining == 0:
            score = await self.llm_service.score_conversation(
                history + [Message(content=message_content, timestamp=datetime.now(UTC))]
            )
            attempt.score = score
        
        self.db.add(new_message)
        self.db.commit()
        self.db.refresh(new_message)
        
        return new_message