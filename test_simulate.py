# test_simulate.py
import asyncio
import httpx
from uuid import uuid4
from datetime import datetime
import random
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
import time
import statistics

async def cleanup_database(client):
    try:
        # Get current active session
        resp = await client.get("/sessions/current")
        if resp.status_code == 200:
            session_data = resp.json()
            # End the session
            await client.put(f"/sessions/{session_data['id']}/end")
    except httpx.HTTPError:
        pass  # No active session

async def send_message_with_retry(client, attempt_id, message, max_retries=3):
    for attempt in range(max_retries):
        try:
            resp = await client.post(
                f"/attempts/{attempt_id}/message",
                json={"content": message},
                timeout=60.0  # Increased timeout
            )
            return resp
        except httpx.ReadTimeout:
            if attempt == max_retries - 1:
                raise
            print(f"Timeout occurred, retrying... ({attempt + 1}/{max_retries})")
            await asyncio.sleep(1)  # Reduced sleep time for concurrent testing
        except Exception as e:
            print(f"Error sending message: {e}")
            raise

async def simulate_user_behavior(
    client: httpx.AsyncClient,
    user_id: str,
    attempt_id: str,
    messages: List[str],
    user_type: str
) -> Dict:
    results = {
        "user_id": user_id,
        "attempt_id": attempt_id,
        "messages_sent": 0,
        "errors": [],
        "responses": [],
        "user_type": user_type,
        "timing": []
    }
    
    for msg in messages:
        try:
            start_time = time.time()
            resp = await send_message_with_retry(client, attempt_id, msg)
            end_time = time.time()
            
            results["timing"].append(end_time - start_time)
            results["messages_sent"] += 1
            
            if resp.status_code != 200:
                results["errors"].append({
                    "status_code": resp.status_code,
                    "detail": resp.json().get("detail", "No detail provided"),
                    "message": msg[:50] + "..."
                })
            else:
                results["responses"].append(resp.json())
                
        except Exception as e:
            results["errors"].append({
                "error_type": type(e).__name__,
                "detail": str(e),
                "message": msg[:50] + "..."
            })
            break
    
    return results

async def simulate_concurrent_session(num_users: int = 10):
    """Simulate multiple users interacting with the system concurrently"""
    
    teaching_strategies = {
        "scientific": [
            "Let me teach you about quantum entanglement and its implications for physics.",
            "Here's how quantum tunneling works in semiconductor devices.",
            "The mathematical foundations of quantum mechanics include...",
        ],
        "historical": [
            "The development of calculus by Newton and Leibniz changed mathematics forever.",
            "Ancient Babylonian mathematics included sophisticated numerical systems.",
            "The history of prime number theory reveals fascinating patterns.",
        ],
        "philosophical": [
            "Consider the philosophical implications of Gödel's incompleteness theorems.",
            "The nature of mathematical truth raises deep epistemological questions.",
            "Here's how the concept of infinity evolved in mathematical thinking.",
        ]
    }
    
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Cleanup and create new session
        await cleanup_database(client)
        session_resp = await client.post("/sessions/create", params={"entry_fee": 10.0})
        session_data = session_resp.json()
        
        # Create users and their attempts
        users = []
        attempts = []
        for i in range(num_users):
            strategy = random.choice(list(teaching_strategies.keys()))
            
            # Create user
            user_resp = await client.post("/users/create", 
                json={"wldd_id": f"WLDD-{uuid4().hex[:8].upper()}"})
            user_data = user_resp.json()
            
            # Create attempt
            attempt_resp = await client.post("/attempts/create", 
                params={"user_id": user_data["id"]})
            attempt_data = attempt_resp.json()
            
            users.append({
                "user_data": user_data,
                "strategy": strategy,
                "messages": teaching_strategies[strategy]
            })
            attempts.append(attempt_data)
        
        # Simulate concurrent user behavior
        tasks = []
        for user, attempt in zip(users, attempts):
            task = simulate_user_behavior(
                client,
                user["user_data"]["id"],
                attempt["id"],
                user["messages"],
                user["strategy"]
            )
            tasks.append(task)
        
        # Run all user simulations concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # End session
        await client.put(f"/sessions/{session_data['id']}/end")
        
        # Analyze results
        print("\nConcurrency Test Results:")
        print(f"Total users: {num_users}")
        
        success_count = sum(1 for r in results if isinstance(r, dict) and not r["errors"])
        error_count = len(results) - success_count
        
        print(f"Successful users: {success_count}")
        print(f"Users with errors: {error_count}")
        
        # Print errors if any
        for result in results:
            if isinstance(result, dict) and result["errors"]:
                print(f"\nUser {result['user_id']} ({result['user_type']}) errors:")
                for error in result["errors"]:
                    print(f"  - {error}")
            elif isinstance(result, Exception):
                print(f"\nTask failed with exception: {result}")

        await analyze_results(results)

async def analyze_results(results: List[Dict]):
    print("\nDetailed Analysis:")
    print("-" * 50)
    
    total_requests = sum(r["messages_sent"] for r in results)
    total_errors = sum(len(r["errors"]) for r in results)
    avg_response_time = statistics.mean(
        t for r in results for t in r["timing"]
    )
    
    print(f"Total Requests: {total_requests}")
    print(f"Successful Requests: {total_requests - total_errors}")
    print(f"Failed Requests: {total_errors}")
    print(f"Average Response Time: {avg_response_time:.2f}s")
    
    if total_errors > 0:
        print("\nError Distribution:")
        error_types = {}
        for r in results:
            for e in r["errors"]:
                error_type = e.get("error_type", "HTTP Error")
                error_types[error_type] = error_types.get(error_type, 0) + 1
        
        for error_type, count in error_types.items():
            print(f"  {error_type}: {count}")

async def run_concurrent_tests():
    """Run multiple concurrent test scenarios"""
    
    test_scenarios = [
        (5, "Small concurrent group"),
        (10, "Medium concurrent group"),
        (20, "Large concurrent group")
    ]
    
    for num_users, scenario_name in test_scenarios:
        print(f"\n=== Testing Scenario: {scenario_name} ({num_users} users) ===")
        await simulate_concurrent_session(num_users)
        await asyncio.sleep(2)  # Brief pause between scenarios

if __name__ == "__main__":
    asyncio.run(run_concurrent_tests())