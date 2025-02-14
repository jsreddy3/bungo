# src/services/llm.py
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from litellm import completion, acompletion
import yaml
from src.models.game import Message
from src.services.exceptions import LLMServiceError
import asyncio
import logging

logger = logging.getLogger("bungo.llm")

class LLMResponse(BaseModel):
    content: str
    model: str
    completion_id: str

class LLMService:
    def __init__(self, prompts_path: str = "prompts.yaml"):
        with open(prompts_path, "r") as f:
            self.prompts = yaml.safe_load(f)
        
    async def process_message(self, message: str, conversation_history: List[Message]) -> LLMResponse:
        conversation_payload = [
            {"role": "system", "content": self.prompts["CONVERSATION_SYSTEM_PROMPT"]}
        ]
        
        for msg in conversation_history:
            conversation_payload.append({"role": "user", "content": msg.content})
            if hasattr(msg, 'ai_response'):
                conversation_payload.append({"role": "assistant", "content": msg.ai_response})
        
        conversation_payload.append({"role": "user", "content": message})
        
        logger.debug("LLM Input Context:")
        for msg in conversation_payload:
            logger.debug(f"{msg['role']}: {msg['content']}")
        
        try:
            response = await acompletion(
                model="gpt-4o",
                messages=conversation_payload
            )
            
            content = response.choices[0].message.content
            logger.debug(f"LLM Output: {content}")
            
            return LLMResponse(
                content=content,
                model=response.model,
                completion_id=response.id
            )
            
        except Exception as e:
            logger.error(f"LLM call failed: {str(e)}")
            raise LLMServiceError(f"Failed to process message: {str(e)}")

    async def score_conversation(
        self, 
        messages: List[Message],
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> float:
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
        
        logger.debug("Scoring LLM Input Context:")
        for msg in judge_prompt:
            logger.debug(f"{msg['role']}: {msg['content']}")
        
        last_error = None
        for attempt in range(max_retries):
            try:
                response = await acompletion(
                    model="gpt-4o",
                    messages=judge_prompt
                )
                
                content = response.choices[0].message.content
                logger.debug(f"Scoring LLM Output: {content}")
                
                import json
                score_data = json.loads(content)
                return float(score_data["score"])
                
            except Exception as e:
                last_error = LLMServiceError(f"Failed to score conversation: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
        
        raise last_error