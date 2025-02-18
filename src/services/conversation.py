# src/services/conversation.py
from typing import List, Optional
from datetime import datetime
from src.models.game import Message
from src.services.llm_service import LLMService
from src.models.database_models import DBMessage, DBAttempt, DBUser
from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import HTTPException
import time
UTC = ZoneInfo("UTC")

class ConversationManager:
    def __init__(self, llm_service: LLMService, db: Session):
        self.llm_service = llm_service
        self.db = db
        
    async def process_attempt_message(self, attempt_id: UUID, message_content: str, user_name: Optional[str] = None) -> DBMessage:
        # First check attempt exists and has messages remaining without a lock
        attempt = self.db.query(DBAttempt).filter(
            DBAttempt.id == attempt_id
        ).first()
        
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found")
            
        if attempt.messages_remaining <= 0:
            raise HTTPException(status_code=400, detail="No messages remaining")
        
        # Get user's language preference
        user = self.db.query(DBUser).filter(DBUser.wldd_id == attempt.wldd_id).first()
        user_language = user.language if user else "english"
        
        # Get conversation history
        print("Attempt messages from DB:")
        for msg in attempt.messages:
            print(f"  content: {msg.content}")
            print(f"  ai_response: {msg.ai_response}")
            
        messages = [Message(
            content=msg.content,
            timestamp=msg.timestamp,
            ai_response=msg.ai_response
        ) for msg in attempt.messages]
        
        print("\nConverted to Message objects:")
        for msg in messages:
            print(f"  content: {msg.content}")
            print(f"  ai_response: {msg.ai_response}")
        
        # Process message with LLM outside of any transaction
        try:
            response = await self.llm_service.process_message(
                message_content,
                messages,
                user_name,
                language=user_language
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM service error: {str(e)}")

        # Now do the database updates with minimal lock time
        try:
            # Lock the row only for the update
            attempt = self.db.query(DBAttempt).with_for_update().filter(
                DBAttempt.id == attempt_id
            ).first()
            
            # Double check messages remaining in case it changed
            if attempt.messages_remaining <= 0:
                raise HTTPException(status_code=400, detail="No messages remaining")
            
            new_message = DBMessage(
                attempt_id=attempt_id,
                content=message_content,
                ai_response=response.content,
                timestamp=datetime.now(UTC)
            )
            
            attempt.messages_remaining -= 1
            
            self.db.add(new_message)
            self.db.commit()
            return new_message
            
        except Exception as e:
            self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")