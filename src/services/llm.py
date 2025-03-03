# src/services/llm.py

import os
from datetime import datetime
from uuid import uuid4
import yaml

from litellm import completion
from src.models.game import GameAttempt, Message

MAX_MESSAGES = 2  # Maximum number of user messages allowed

def load_prompts():
    """Load prompts from prompts.yaml file"""
    with open("prompts.yaml", "r") as f:
        return yaml.safe_load(f)

PROMPTS = load_prompts()

def get_conversation_score(messages: list[Message]) -> float:
    """
    Send the conversation to a judge (LLM) to evaluate how well the user taught something new.
    Returns a score from 0 to 10.
    """
    # Convert the conversation into a readable format for the judge
    conversation_text = "\n".join([
        f"{'User' if i % 2 == 0 else 'AI'}: {msg.content}"
        for i, msg in enumerate(messages)
    ])
    
    judge_prompt = [
        {"role": "system", "content": PROMPTS["JUDGE_SYSTEM_PROMPT"]},
        {"role": "user", "content": PROMPTS["JUDGE_USER_PROMPT"].format(conversation=conversation_text)}
    ]
    
    try:
        response = completion(model="gpt-4o-mini", messages=judge_prompt)
        return response.choices[0].message.content
    except Exception as e:
        print("Failed to get judge's score:", e)
        return "Score: 0/10\nReason: Failed to evaluate due to technical error."

def run_conversation():
    if "OPENAI_API_KEY" not in os.environ:
        print("OPENAI_API_KEY not set; please set it to your API key for LiteLLM.")
        return

    # Create a new game attempt with dummy session and user ids.
    attempt = GameAttempt(session_id=uuid4(), user_id=uuid4(), messages=[], messages_remaining=MAX_MESSAGES)

    print("Starting game conversation!")
    print(f"You have {MAX_MESSAGES} messages to try to teach the AI something new.")
    
    user_messages_count = 0
    # Initialize conversation with system prompt
    conversation_payload = [{"role": "system", "content": PROMPTS["CONVERSATION_SYSTEM_PROMPT"]}]
    
    while user_messages_count < MAX_MESSAGES:
        user_input = input("You: ")
        if not user_input:
            print("Empty input detected. Ending conversation.")
            break
        
        user_messages_count += 1
        user_msg = Message(content=user_input, timestamp=datetime.utcnow())
        attempt.messages.append(user_msg)
        
        # Add user message to conversation payload
        conversation_payload.append({"role": "user", "content": user_input})
        
        print("Sending conversation to LLM:", conversation_payload)
        try:
            response = completion(model="gpt-4o-mini", messages=conversation_payload)
            ai_response = response.choices[0].message.content
            print("AI:", ai_response)
        except Exception as e:
            print("LLM API call failed:", e)
            break
        
        ai_msg = Message(content=ai_response, timestamp=datetime.utcnow())
        attempt.messages.append(ai_msg)
        conversation_payload.append({"role": "assistant", "content": ai_response})
        
        remaining_messages = MAX_MESSAGES - user_messages_count
        print(f"You have {remaining_messages} message{'s' if remaining_messages != 1 else ''} remaining.")
    
    print("\nGame conversation ended!")
    print("\nJudging your teaching attempt...")
    score = get_conversation_score(attempt.messages)
    print("\nJUDGE'S EVALUATION:")
    print(score)

if __name__ == "__main__":
    run_conversation()
