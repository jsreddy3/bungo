# src/services/llm.py
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from litellm import completion, acompletion, completion_cost
import yaml
from src.models.game import Message
from src.services.exceptions import LLMServiceError
import asyncio
import logging

# Configure logging to suppress debug messages from external libraries
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('litellm').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)

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
        language: str = "english",
        is_free_attempt: bool = False,
    ) -> LLMResponse:
        # Map language codes to full names if needed
        language_map = {
            "en": "english",
            "es": "spanish",
            "pt": "portuguese",
            "english": "english",
            "spanish": "spanish",
            "portuguese": "portuguese"
        }
        language = language_map.get(language.lower(), language.lower())
        
        # Map full names to language codes
        language_codes = {
            "english": "EN",
            "spanish": "ES",
            "portuguese": "PT"
        }
        
        # Get the appropriate system prompt for the language
        system_prompt_key = f"CONVERSATION_SYSTEM_PROMPT_{language_codes[language]}"
        system_prompt = self.prompts.get(system_prompt_key, self.prompts["CONVERSATION_SYSTEM_PROMPT_EN"])
        
        if user_name:
            # Use "the user" as default if no name provided
            system_prompt = system_prompt.replace("{user_name}", user_name)
        else:
            system_prompt = system_prompt.replace("{user_name}", "the user")
        
        # logger.debug(f"Using system prompt with user name: {system_prompt[:100]}...")  # Log first 100 chars
        
        conversation_payload = [
            {"role": "system", "content": system_prompt}
        ]
        
        for msg in conversation_history:
            # logger.debug(f"\nMessage from history:")
            # logger.debug(f"  content: {msg.content}")
            # logger.debug(f"  ai_response: {msg.ai_response}")
            conversation_payload.append({"role": "user", "content": msg.content})
            if msg.ai_response is not None:
                conversation_payload.append({"role": "assistant", "content": msg.ai_response})
        
        conversation_payload.append({"role": "user", "content": message})
        
        logger.debug("\nFinal conversation payload:")
        for msg in conversation_payload:
            logger.debug(f"  {msg['role']}: {msg['content'][:100]}...")
        
        try:
            response = await acompletion(
                model="gpt-4o-mini" if is_free_attempt else "chatgpt-4o-latest",
                messages=conversation_payload
            )

            cost = 0
            
            content = response.choices[0].message.content
            logger.debug(f"LLM Output: {content}")
            
            return LLMResponse(
                content=content,
                model=response.model,
                completion_id=response.id,
            ), cost
            
        except Exception as e:
            logger.error(f"LLM call failed: {str(e)}")
            raise LLMServiceError(f"Failed to process message: {str(e)}")

    async def score_conversation(
        self, 
        messages: List[Message],
        language: str = "english",
        max_retries: int = 5,
        base_delay: float = 0.2
    ) -> float:
        # Map language codes to full names if needed
        language_map = {
            "en": "english",
            "es": "spanish",
            "pt": "portuguese",
            "english": "english",
            "spanish": "spanish",
            "portuguese": "portuguese"
        }
        language = language_map.get(language.lower(), language.lower())
        
        # Map full names to language codes
        language_codes = {
            "english": "EN",
            "spanish": "ES",
            "portuguese": "PT"
        }
        
        conversation_text = "\n".join([
            f"{'User' if i % 2 == 0 else 'AI'}: {msg.content}"
            for i, msg in enumerate(messages)
        ])
        
        # Get the appropriate judge prompts for the language
        judge_system_prompt_key = f"JUDGE_SYSTEM_PROMPT_{language_codes[language]}"
        judge_user_prompt_key = f"JUDGE_USER_PROMPT_{language_codes[language]}"
        
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

                cost = 0

                import json
                score_data = json.loads(content)
                return float(score_data["score"]), cost
                
            except Exception as e:
                last_error = LLMServiceError(f"Failed to score conversation: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))
        
        raise last_error