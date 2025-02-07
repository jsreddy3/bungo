# src/services/llm.py
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from litellm import completion
import yaml
from src.models.game import Message
from src.services.exceptions import LLMServiceError

class LLMResponse(BaseModel):
    content: str
    model: str
    completion_id: str

class LLMService:
    def __init__(self, prompts_path: str = "prompts.yaml"):
        self.prompts = self._load_prompts(prompts_path)
        
    def _load_prompts(self, path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)
    
    async def process_message(
        self, 
        message: str, 
        conversation_history: List[Message]
    ) -> LLMResponse:
        """Process a single message in context of conversation history"""
        
        # Build conversation payload
        conversation_payload = [
            {"role": "system", "content": self.prompts["CONVERSATION_SYSTEM_PROMPT"]}
        ]
        
        # Add conversation history
        for msg in conversation_history:
            conversation_payload.append({"role": "user", "content": msg.content})
            if msg.ai_response:
                conversation_payload.append({"role": "assistant", "content": msg.ai_response})
        
        # Add current message
        conversation_payload.append({"role": "user", "content": message})
        
        try:
            response = await completion(
                model="gpt-4o-mini", 
                messages=conversation_payload
            )
            
            return LLMResponse(
                content=response.choices[0].message.content,
                model=response.model,
                completion_id=response.id
            )
        except Exception as e:
            # Log error appropriately
            raise LLMServiceError(f"Failed to process message: {str(e)}")

    async def score_conversation(
        self, 
        messages: List[Message]
    ) -> float:
        """Score the teaching effectiveness of a conversation"""
        
        conversation_text = "\n".join([
            f"{'User' if i % 2 == 0 else 'AI'}: {msg.content}"
            for i, msg in enumerate(messages)
        ])
        
        judge_prompt = [
            {"role": "system", "content": self.prompts["JUDGE_SYSTEM_PROMPT"]},
            {
                "role": "user", 
                "content": self.prompts["JUDGE_USER_PROMPT"].format(
                    conversation=conversation_text
                )
            }
        ]
        
        try:
            response = await completion(
                model="gpt-4o-mini", 
                messages=judge_prompt
            )
            
            # Assume response is in format "Score: X/10\nReason: ..."
            score_text = response.choices[0].message.content
            score = float(score_text.split("/")[0].split(":")[1].strip())
            return score
            
        except Exception as e:
            # Log error appropriately
            raise LLMServiceError(f"Failed to score conversation: {str(e)}")