# src/services/conversation.py
from typing import List, Optional
from datetime import datetime
from src.models.game import Message
from src.services.llm_service import LLMService
from src.models.database_models import DBMessage, DBAttempt
from sqlalchemy.orm import Session
from sqlalchemy import select
from uuid import UUID
from zoneinfo import ZoneInfo
from fastapi import HTTPException
UTC = ZoneInfo("UTC")

class ConversationManager:
    def __init__(self, llm_service: LLMService, db: Session):
        self.llm_service = llm_service
        self.db = db
        
    async def process_attempt_message(self, attempt_id: UUID, message_content: str) -> DBMessage:
        try:
            # Start transaction and lock the attempt
            attempt = self.db.query(DBAttempt).with_for_update().filter(
                DBAttempt.id == attempt_id
            ).first()
            
            if not attempt:
                raise HTTPException(status_code=404, detail="Attempt not found")
                
            if attempt.messages_remaining <= 0:
                raise HTTPException(status_code=400, detail="No messages remaining")
            
            # Process message with retries
            try:
                response = await self.llm_service.process_message(
                    message_content,
                    [Message(content=msg.content, timestamp=msg.timestamp)
                     for msg in attempt.messages]
                )
            except Exception as e:
                self.db.rollback()
                raise HTTPException(status_code=503, detail=f"LLM service error: {str(e)}")
            
            new_message = DBMessage(
                attempt_id=attempt_id,
                content=message_content,
                ai_response=response.content,
                timestamp=datetime.now(UTC)
            )
            
            attempt.messages_remaining -= 1
            
            if attempt.messages_remaining == 0:
                try:
                    score = await self.llm_service.score_conversation(
                        attempt.messages + [Message(content=message_content, timestamp=datetime.now(UTC))]
                    )
                    attempt.score = score
                except Exception as e:
                    self.db.rollback()
                    raise HTTPException(status_code=503, detail=f"Scoring error: {str(e)}")
            
            self.db.add(new_message)
            self.db.commit()
            return new_message
            
        except HTTPException:
            raise
        except Exception as e:
            self.db.rollback()
            raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")