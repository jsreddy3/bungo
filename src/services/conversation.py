# src/services/conversation.py
from typing import List, Optional
from datetime import datetime
from src.models.game import Message, GameAttempt
from src.services.llm import LLMService
from src.models.database_models import DBMessage, DBAttempt
from sqlalchemy.orm import Session

class ConversationManager:
    def __init__(self, llm_service: LLMService, db: Session):
        self.llm_service = llm_service
        self.db = db
        
    async def process_attempt_message(
        self, 
        attempt_id: UUID, 
        message_content: str
    ) -> Message:
        """Process a message for a given attempt"""
        
        # Get attempt and validate
        attempt = self.db.query(DBAttempt).filter(
            DBAttempt.id == attempt_id
        ).first()
        
        if not attempt:
            raise ValueError("Attempt not found")
            
        if attempt.messages_remaining <= 0:
            raise ValueError("No messages remaining")
        
        # Get conversation history
        history = self.db.query(DBMessage).filter(
            DBMessage.attempt_id == attempt_id
        ).order_by(DBMessage.timestamp.asc()).all()
        
        # Process message
        response = await self.llm_service.process_message(
            message_content,
            history
        )
        
        # Create new message
        new_message = DBMessage(
            attempt_id=attempt_id,
            content=message_content,
            ai_response=response.content,
            timestamp=datetime.now(UTC)
        )
        
        # Update attempt
        attempt.messages_remaining -= 1
        
        # If final message, score conversation
        if attempt.messages_remaining == 0:
            score = await self.llm_service.score_conversation(
                history + [new_message]
            )
            attempt.score = score
        
        # Save to DB
        self.db.add(new_message)
        self.db.commit()
        
        return new_message