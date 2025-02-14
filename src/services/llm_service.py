# src/services/llm.py
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from litellm import completion, acompletion
import yaml
from src.models.game import Message
from src.services.exceptions import LLMServiceError
import asyncio

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
    
    async def process_message(self, message: str, conversation_history: List[Message]) -> LLMResponse:
        conversation_payload = [
            {"role": "system", "content": self.prompts["CONVERSATION_SYSTEM_PROMPT"]}
        ]
        
        for msg in conversation_history:
            conversation_payload.append({"role": "user", "content": msg.content})
            if hasattr(msg, 'ai_response'):  # Check if attribute exists
                conversation_payload.append({"role": "assistant", "content": msg.ai_response})
        
        conversation_payload.append({"role": "user", "content": message})
        
        try:
            response = await acompletion(  # Using acompletion
                model="gpt-4o",
                messages=conversation_payload
            )
            
            return LLMResponse(
                content=response.choices[0].message.content,
                model=response.model,
                completion_id=response.id
            )
        except Exception as e:
            raise LLMServiceError(f"Failed to process message: {str(e)}")

    async def score_conversation(
        self, 
        messages: List[Message],
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> float:
        """Score the teaching effectiveness of a conversation
        
        Args:
            messages: List of messages in the conversation
            max_retries: Maximum number of retry attempts (default: 3)
            base_delay: Base delay for exponential backoff in seconds (default: 1.0)
        """
        
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
        
        last_error = None
        for attempt in range(max_retries):
            try:
                response = await acompletion(
                    model="gpt-4o",
                    messages=judge_prompt
                )
                
                # Parse JSON response
                import json
                score_data = json.loads(response.choices[0].message.content)
                return float(score_data["score"])
                
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                last_error = LLMServiceError(f"Invalid scoring format: {str(e)}")
            except Exception as e:
                last_error = LLMServiceError(f"Failed to score conversation: {str(e)}")
            
            if attempt < max_retries - 1:  # Don't sleep on the last attempt
                delay = base_delay * (2 ** attempt)  # Exponential backoff
                await asyncio.sleep(delay)
        
        raise last_error  # Re-raise the last error if all retries failed