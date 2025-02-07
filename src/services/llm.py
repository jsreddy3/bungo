import os
from datetime import datetime
from uuid import uuid4

from litellm import completion
from src.models.game import GameAttempt, Message


def run_conversation():
    if "OPENAI_API_KEY" not in os.environ:
        print("OPENAI_API_KEY not set; please set it to your API key for LiteLLM.")
        return

    # Create a new game attempt with dummy session and user ids.
    attempt = GameAttempt(session_id=uuid4(), user_id=uuid4(), messages=[], messages_remaining=5)

    print("Starting game conversation!")
    print(f"You can have up to {5 // 2} rounds of conversation (2 messages per round, max 5 messages allowed).")
    
    # Loop will allow conversation rounds as long as adding a user message and an AI response does not exceed the 5 message limit.
    while len(attempt.messages) <= 3:  # ensures room for 2 more messages
        user_input = input("You: ")
        if not user_input:
            print("Empty input detected. Ending conversation.")
            break
        
        # Check if adding the user's message would exceed the maximum allowed messages
        if len(attempt.messages) + 1 > 5:
            print("Reached the maximum messages limit. Ending conversation.")
            break
        
        user_msg = Message(content=user_input, timestamp=datetime.utcnow())
        attempt.messages.append(user_msg)
        
        # Build conversation payload by inferring roles based on message order: even index -> user, odd index -> assistant
        conversation_payload = []
        for i, msg in enumerate(attempt.messages):
            role = "user" if i % 2 == 0 else "assistant"
            conversation_payload.append({"role": role, "content": msg.content})
        
        print("Sending conversation to LLM:", conversation_payload)
        try:
            response = completion(model="gpt-4o-mini", messages=conversation_payload)
            ai_response = response.choices[0].message.content
            print("AI:", ai_response)
        except Exception as e:
            print("LLM API call failed:", e)
            break
        
        # Check if adding the AI response will exceed the maximum allowed messages
        if len(attempt.messages) + 1 > 5:
            print("Reached the maximum messages limit. Cannot record AI response.")
            break
        
        ai_msg = Message(content=ai_response, timestamp=datetime.utcnow())
        attempt.messages.append(ai_msg)
        
        print(f"Conversation now has {len(attempt.messages)} messages. Remaining allowed: {5 - len(attempt.messages)}")
    
    print("Game conversation ended!")
    

if __name__ == "__main__":
    run_conversation()
