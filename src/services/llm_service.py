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
        
    async def process_message(
        self, 
        message: str, 
        conversation_history: List[Message], 
        user_name: Optional[str] = None,
        language: str = "english"
    ) -> LLMResponse:
        # Get the appropriate system prompt for the language
        system_prompt_key = f"CONVERSATION_SYSTEM_PROMPT_{language.upper()}"
        system_prompt = self.prompts.get(system_prompt_key, self.prompts["CONVERSATION_SYSTEM_PROMPT_EN"])
        
        if user_name:
            # Use "the user" as default if no name provided
            system_prompt = system_prompt.replace("{user_name}", user_name)
        else:
            system_prompt = system_prompt.replace("{user_name}", "the user")
        
        logger.debug(f"Using system prompt with user name: {system_prompt[:100]}...")  # Log first 100 chars
        
        conversation_payload = [
            {"role": "system", "content": system_prompt}
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
                model="chatgpt-4o-latest",
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
        language: str = "english",
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> float:
        conversation_text = "\n".join([
            f"{'User' if i % 2 == 0 else 'AI'}: {msg.content}"
            for i, msg in enumerate(messages)
        ])
        
        # Get the appropriate judge prompts for the language
        judge_system_prompt_key = f"JUDGE_SYSTEM_PROMPT_{language.upper()}"
        judge_user_prompt_key = f"JUDGE_USER_PROMPT_{language.upper()}"
        
        judge_system_prompt = self.prompts.get(judge_system_prompt_key, self.prompts["JUDGE_SYSTEM_PROMPT_EN"])
        judge_user_prompt = self.prompts.get(judge_user_prompt_key, self.prompts["JUDGE_USER_PROMPT_EN"])
        
        judge_prompt = [
            {"role": "system", "content": judge_system_prompt},
            {
                "role": "user", 
                "content": judge_user_prompt.format(
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