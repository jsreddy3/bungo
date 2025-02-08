# src/services/llm.py
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
import yaml
import openai
import os
from src.models.game import Message
from src.services.exceptions import LLMServiceError

# Set Fireworks API details
openai.api_base = "https://api.fireworks.ai/inference/v1"
openai.api_key = os.getenv("FIREWORKS_API_KEY")

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
            if hasattr(msg, 'ai_response'):
                conversation_payload.append({"role": "assistant", "content": msg.ai_response})
        
        conversation_payload.append({"role": "user", "content": message})
        
        try:
            response = await openai.ChatCompletion.acreate(  # Using async OpenAI client
                model="accounts/fireworks/models/gpt-4o",
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
            response = await openai.ChatCompletion.acreate(
                model="accounts/fireworks/models/gpt-4o",
                messages=judge_prompt
            )
            
            # Parse JSON response
            import json
            score_data = json.loads(response.choices[0].message.content)
            return float(score_data["score"])
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse score response: {response.choices[0].message.content}")
            raise LLMServiceError(f"Invalid scoring format: {str(e)}")
        except Exception as e:
            raise LLMServiceError(f"Failed to score conversation: {str(e)}")