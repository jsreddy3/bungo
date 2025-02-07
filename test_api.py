# test_api.py
import asyncio
import httpx
from uuid import uuid4
from datetime import datetime

async def test_flow():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        print("Testing API flow...")
        
        # 1. Check health
        print("\nTesting health check...")
        resp = await client.get("/health")
        print(f"Health check response: {resp.json()}")
        
        # 2. Create user
        print("\nCreating test user...")
        wldd_id = f"WLDD-{uuid4().hex[:8].upper()}"
        resp = await client.post(
            "/users/create", 
            json={"wldd_id": wldd_id}
        )
        user_data = resp.json()
        user_id = user_data["id"]
        print(f"Created user: {user_data}")
        
        # 3. Create session
        print("\nCreating game session...")
        resp = await client.post("/sessions/create", params={"entry_fee": 10.0})
        session_data = resp.json()
        session_id = session_data["id"]
        print(f"Created session: {session_data}")
        
        # 4. Create attempt
        print("\nCreating attempt...")
        resp = await client.post("/attempts/create", params={"user_id": user_id})
        attempt_data = resp.json()
        attempt_id = attempt_data["id"]
        print(f"Created attempt: {attempt_data}")
        
        # 5. Submit messages
        test_messages = [
            "The sky is actually green, not blue",
            "Here's the scientific evidence...",
            "Let me explain further...",
            "Consider this perspective...",
            "Final convincing argument..."
        ]
        
        print("\nSubmitting messages...")
        for msg in test_messages:
            print(f"\nSending message: {msg}")
            try:
                resp = await client.post(
                    f"/attempts/{attempt_id}/message",
                    json={"content": msg},
                    timeout=30.0  # Add timeout for LLM processing
                )
                if resp.status_code == 200:
                    print(f"Success! AI Response: {resp.json()}")
                else:
                    print(f"Error: {resp.status_code} - {resp.text}")
                
                # Add delay between messages to prevent rate limiting
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"Error sending message: {e}")
                break
            
        # 6. Check attempt score
        print("\nChecking attempt score...")
        resp = await client.get(f"/attempts/{attempt_id}")
        print(f"Final attempt data: {resp.json()}")
        
        # 7. End session
        print("\nEnding session...")
        resp = await client.put(f"/sessions/{session_id}/end")
        print(f"Session end response: {resp.json()}")
        
        # 8. Check user stats
        print("\nChecking user stats...")
        resp = await client.get(f"/users/{user_id}/stats")
        print(f"User stats: {resp.json()}")

if __name__ == "__main__":
    asyncio.run(test_flow())