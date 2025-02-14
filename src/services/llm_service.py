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
        logger.info(f"Initializing LLM service with prompts from {prompts_path}")
        self.prompts = self._load_prompts(prompts_path)
        
    def _load_prompts(self, path: str) -> dict:
        try:
            with open(path, "r") as f:
                prompts = yaml.safe_load(f)
                logger.debug(f"Loaded prompts: {prompts.keys()}")
                return prompts
        except Exception as e:
            logger.error(f"Failed to load prompts from {path}: {e}")
            raise
    
    async def process_message(self, message: str, conversation_history: List[Message]) -> LLMResponse:
        logger.info("Processing new message")
        logger.debug(f"Message content: {message}")
        logger.debug(f"Conversation history length: {len(conversation_history)}")
        
        conversation_payload = [
            {"role": "system", "content": self.prompts["CONVERSATION_SYSTEM_PROMPT"]}
        ]
        
        for msg in conversation_history:
            conversation_payload.append({"role": "user", "content": msg.content})
            if hasattr(msg, 'ai_response'):
                conversation_payload.append({"role": "assistant", "content": msg.ai_response})
        
        conversation_payload.append({"role": "user", "content": message})
        
        logger.debug(f"Full conversation payload: {conversation_payload}")
        
        try:
            logger.info("Sending request to LLM")
            response = await acompletion(
                model="gpt-4o",
                messages=conversation_payload
            )
            
            logger.debug(f"Raw LLM response: {response}")
            
            llm_response = LLMResponse(
                content=response.choices[0].message.content,
                model=response.model,
                completion_id=response.id
            )
            
            logger.info(f"Successfully processed message. Completion ID: {llm_response.completion_id}")
            return llm_response
            
        except Exception as e:
            logger.error(f"Failed to process message: {str(e)}", exc_info=True)
            raise LLMServiceError(f"Failed to process message: {str(e)}")

    async def score_conversation(
        self, 
        messages: List[Message],
        max_retries: int = 3,
        base_delay: float = 1.0
    ) -> float:
        logger.info("Scoring conversation")
        logger.debug(f"Number of messages to score: {len(messages)}")
        
        conversation_text = "\n".join([
            f"{'User' if i % 2 == 0 else 'AI'}: {msg.content}"
            for i, msg in enumerate(messages)
        ])
        
        logger.debug(f"Formatted conversation: {conversation_text}")
        
        judge_prompt = [
            {"role": "system", "content": self.prompts["JUDGE_SYSTEM_PROMPT"]},
            {
                "role": "user", 
                "content": self.prompts["JUDGE_USER_PROMPT"].format(
                    conversation=conversation_text
                )
            }
        ]
        
        logger.debug(f"Judge prompt: {judge_prompt}")
        
        last_error = None
        for attempt in range(max_retries):
            try:
                logger.info(f"Scoring attempt {attempt + 1}/{max_retries}")
                response = await acompletion(
                    model="gpt-4o",
                    messages=judge_prompt
                )
                
                logger.debug(f"Raw scoring response: {response}")
                
                # Parse JSON response
                import json
                score_data = json.loads(response.choices[0].message.content)
                score = float(score_data["score"])
                
                logger.info(f"Successfully scored conversation. Score: {score}")
                return score
                
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.warning(f"Invalid scoring format on attempt {attempt + 1}: {str(e)}")
                last_error = LLMServiceError(f"Invalid scoring format: {str(e)}")
            except Exception as e:
                logger.error(f"Failed to score conversation on attempt {attempt + 1}: {str(e)}", exc_info=True)
                last_error = LLMServiceError(f"Failed to score conversation: {str(e)}")
            
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.info(f"Retrying in {delay} seconds...")
                await asyncio.sleep(delay)
        
        logger.error("All scoring attempts failed")
        raise last_error