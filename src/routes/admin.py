from fastapi import APIRouter, Depends, HTTPException, Security, BackgroundTasks
from fastapi.security import APIKeyHeader
from src.database import get_db
from src.models.game import SessionStatus
from src.models.database_models import DBSession, DBAttempt, DBUser, DBMessage, DBVerification
from src.services.llm_service import LLMService
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import UUID
from tabulate import tabulate
import os
from sqlalchemy.orm import Session
import random
import asyncio

router = APIRouter(prefix="/admin")

# Set up API key header properly
API_KEY = os.getenv("ADMIN_API_KEY")
API_KEY_NAME = "X-Admin-Key"  # Match frontend
api_key_header = APIKeyHeader(name=API_KEY_NAME)

UTC = ZoneInfo("UTC")

async def get_api_key(api_key_header: str = Security(api_key_header)):
    """Verify admin API key from header"""
    if not API_KEY or api_key_header != API_KEY:
        raise HTTPException(
            status_code=401,  # Changed from 403 to match the error
            detail="Not authenticated"
        )
    return api_key_header

@router.post("/sessions/create")
async def admin_create_session(
    entry_fee: float = 10.0,
    duration_hours: int = 1,
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """Create a new active session"""
    try:
        print(f"Creating session with entry_fee={entry_fee}, duration={duration_hours}")  # Debug log
        
        # Check for active session
        active_session = db.query(DBSession).filter(
            DBSession.status == SessionStatus.ACTIVE.value
        ).first()
        
        if active_session:
            raise HTTPException(
                status_code=400, 
                detail=f"Active session already exists (ID: {active_session.id})"
            )
        
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=duration_hours)
        
        new_session = DBSession(
            start_time=start_time,
            end_time=end_time,
            entry_fee=entry_fee,
            status=SessionStatus.ACTIVE.value
        )
        print(f"Created session object with entry_fee_raw={new_session.entry_fee_raw}")  # Debug log
        
        db.add(new_session)
        db.commit()
        print("Session committed to database")  # Debug log
        
        return {
            "message": "Session created successfully",
            "session_id": new_session.id,
            "start_time": start_time,
            "end_time": end_time,
            "entry_fee": entry_fee
        }
    except Exception as e:
        print(f"Error creating session: {str(e)}")  # Debug log
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

async def create_next_session(delay_minutes: int = 1):
    print(f"Scheduling next session in {delay_minutes} minutes...")
    await asyncio.sleep(delay_minutes * 60)
    
    db = next(get_db())
    try:
        api_key = os.getenv("ADMIN_API_KEY")
        if not api_key:
            print("Error: No ADMIN_API_KEY found in environment")
            return
            
        print("Creating next session...")
        await admin_create_session(
            entry_fee=0.1,
            duration_hours=1,
            api_key=api_key,
            db=db
        )
        print("Next session created successfully")
    except Exception as e:
        print(f"Error creating next session: {str(e)}")
    finally:
        db.close()

@router.post("/sessions/{session_id}/end")
async def admin_end_session(
    session_id: UUID,
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """End a specific session"""
    print(f"=== Ending Session {session_id} ===")
    
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    print(f"Total pot (raw): {session.total_pot_raw}")
    print(f"Total pot (USDC): {session.total_pot}")
    
    # Get all scored attempts
    attempts = db.query(DBAttempt).filter(
        DBAttempt.session_id == session_id,
        DBAttempt.score.isnot(None)
    ).order_by(DBAttempt.score.desc()).all()
    
    # Separate paid and free attempts
    paid_attempts = [a for a in attempts if not a.is_free_attempt]
    free_attempts = [a for a in attempts if a.is_free_attempt]
    
    print(f"\nFound {len(attempts)} total scored attempts:")
    print(f"- {len(paid_attempts)} paid attempts")
    print(f"- {len(free_attempts)} free attempts")
    
    if attempts:
        # Calculate total score across paid attempts only
        total_score = sum(attempt.score for attempt in paid_attempts)
        print(f"\nTotal score across paid attempts: {total_score}")
        pot = session.total_pot
        print(f"Pot to distribute: {pot} USDC")
        
        # Set all free attempts to 0 earnings
        for attempt in free_attempts:
            attempt.earnings = 0

        # First pass - calculate initial shares and identify low scores
        if total_score > 0:
            min_threshold_usdc = 0.1
            saved_earnings = 0  # From < 0.1 USDC attempts
            low_score_earnings = 0  # From scores 3-4
            qualifying_score_total = 0
            qualifying_attempts = []
            
            for attempt in paid_attempts:
                share = (attempt.score / total_score) * pot
                if share < min_threshold_usdc:
                    attempt.earnings = 0
                    saved_earnings += share
                    print(f"\nPaid attempt {attempt.id} below threshold:")
                    print(f"  Score: {attempt.score}")
                    print(f"  Would earn: {share:.4f} USDC (below {min_threshold_usdc} USDC threshold)")
                elif attempt.score <= 4:
                    attempt.earnings = 0
                    low_score_earnings += share
                    print(f"\nPaid attempt {attempt.id} low score (3-4):")
                    print(f"  Score: {attempt.score}")
                    print(f"  Would earn: {share:.4f} USDC (50% to devs, 50% redistributed)")
                else:
                    qualifying_attempts.append(attempt)
                    qualifying_score_total += attempt.score
                    print(f"\nQualifying attempt {attempt.id}:")
                    print(f"  Score: {attempt.score}")
                    print(f"  Initial share: {share:.4f} USDC")
            
            # Calculate amount to redistribute (50% of low score earnings)
            redistribution_amount = low_score_earnings * 0.5
            dev_earnings = low_score_earnings * 0.5
            print(f"\nLow score earnings (scores 3-4): {low_score_earnings:.4f} USDC")
            print(f"Amount to redistribute: {redistribution_amount:.4f} USDC")
            print(f"Amount to developers: {dev_earnings:.4f} USDC")
            
            # Second pass - distribute to qualifying attempts (score >= 5)
            if qualifying_attempts:
                # Add redistribution amount to the qualifying attempts
                for attempt in qualifying_attempts:
                    base_share = (attempt.score / total_score) * pot
                    bonus_share = (attempt.score / qualifying_score_total) * redistribution_amount
                    total_share = base_share + bonus_share
                    print(f"\nCalculating final share for attempt {attempt.id}:")
                    print(f"  Score: {attempt.score}")
                    print(f"  Base share: {base_share:.4f} USDC")
                    print(f"  Bonus share: {bonus_share:.4f} USDC")
                    print(f"  Total share: {total_share:.4f} USDC")
                    attempt.earnings = total_share
                    print(f"  Earnings stored (raw): {attempt.earnings_raw}")
            
            print(f"\nFinal distribution:")
            print(f"  Total pot: {pot:.4f} USDC")
            print(f"  Paid to attempts: {(pot - saved_earnings - dev_earnings):.4f} USDC")
            print(f"  Saved (< 0.1 USDC): {saved_earnings:.4f} USDC")
            print(f"  To developers: {dev_earnings:.4f} USDC")
        else:
            print(f"No paid attempts with scores > 0 found for session {session_id}")
        
        # Find highest scoring attempt among ALL attempts for winning conversation
        max_score = attempts[0].score
        top_attempts = [a for a in attempts if a.score == max_score]
        winning_attempt = random.choice(top_attempts)
        session.winning_attempt_id = winning_attempt.id
        print(f"\nSelected winning attempt {winning_attempt.id} {'(free attempt)' if winning_attempt.is_free_attempt else '(paid attempt)'}")
    
    session.status = SessionStatus.COMPLETED.value
    db.commit()
    
    return {
        "message": "Session ended",
        "session_id": session_id,
        "final_pot": session.total_pot,
        "total_attempts": len(attempts),
        "paid_attempts": len(paid_attempts),
        "free_attempts": len(free_attempts),
        "highest_score": max((a.score for a in attempts), default=0) if attempts else None,
        "winning_attempt_id": session.winning_attempt_id,
        "winning_attempt_was_free": winning_attempt.is_free_attempt if winning_attempt else None
    }

@router.get("/sessions")
async def list_sessions(
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """List all sessions"""
    sessions = db.query(DBSession).order_by(DBSession.start_time.desc()).all()
    
    session_data = []
    for session in sessions:
        data = {
            "id": str(session.id),
            "status": session.status,
            "start_time": session.start_time.strftime("%Y-%m-%d %H:%M"),
            "end_time": session.end_time.strftime("%Y-%m-%d %H:%M"),
            "entry_fee": session.entry_fee,
            "total_pot": session.total_pot,
            "total_attempts": len(session.attempts),
            "scored_attempts": len([a for a in session.attempts if a.score is not None])
        }
        
        # Only include winner info for completed sessions
        if session.status == SessionStatus.COMPLETED.value and session.winning_attempt:
            data["winning_attempt_id"] = str(session.winning_attempt_id)
            data["highest_score"] = session.winning_attempt.score
            
        session_data.append(data)
    
    return session_data

@router.get("/sessions/{session_id}")
async def get_session_details(
    session_id: UUID,
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """Get detailed information about a specific session"""
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    attempts = []
    for attempt in session.attempts:
        messages = [
            {
                "content": msg.content,
                "ai_response": msg.ai_response
            } for msg in attempt.messages
        ]
        attempts.append({
            "id": str(attempt.id),
            "wldd_id": attempt.wldd_id,
            "score": attempt.score or "Not scored",
            "message_count": len(messages),
            "messages": messages,
            "remaining": attempt.messages_remaining,
            "is_winner": bool(attempt.score and attempt.score > 7.0),
            "earnings_raw": attempt.earnings_raw
        })

    return {
        "session": {
            "id": str(session.id),
            "status": session.status,
            "start_time": session.start_time.strftime("%Y-%m-%d %H:%M"),
            "end_time": session.end_time.strftime("%Y-%m-%d %H:%M"),
            "entry_fee": session.entry_fee,
            "total_pot": session.total_pot
        },
        "attempts": attempts
    }

@router.post("/add-verification")
async def add_verification(
    nullifier_hash: str,
    api_key: str = Depends(get_api_key),
    db: Session = Depends(get_db)
):
    """Manually add a verification for testing"""
    print(f"Adding verification for hash: {nullifier_hash}")
    verification = DBVerification(
        nullifier_hash=nullifier_hash,
        merkle_root="0x29334c9988e5ff13fb0d9531bc6a2ed372a89dcd30ef47d74eee528e28f08648",
        action="enter",
        created_at=datetime.now(UTC)
    )
    
    # Also create user if doesn't exist
    user = db.query(DBUser).filter(DBUser.wldd_id == nullifier_hash).first()
    if not user:
        user = DBUser(
            wldd_id=nullifier_hash,
            created_at=datetime.now(UTC),
            last_active=datetime.now(UTC)
        )
        db.add(user)
    
    db.add(verification)
    db.commit()
    
    return {
        "success": True,
        "verification": {
            "nullifier_hash": verification.nullifier_hash,
            "merkle_root": verification.merkle_root,
            "action": verification.action
        }
    }

@router.get("/users")
async def list_users(
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """List all users"""
    users = db.query(DBUser).order_by(DBUser.created_at.desc()).all()
    
    return [{
        "wldd_id": user.wldd_id,
        "created_at": user.created_at,
        "last_active": user.last_active,
        "total_attempts": len(user.attempts),
        "total_earnings": sum(a.earnings for a in user.attempts if a.earnings),
        "best_score": max((a.score for a in user.attempts if a.score), default=0)
    } for user in users]

@router.get("/users/{wldd_id}")
async def get_user_details(
    wldd_id: str,
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """Get detailed user information"""
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "wldd_id": user.wldd_id,
        "created_at": user.created_at,
        "last_active": user.last_active,
        "attempts": [{
            "id": attempt.id,
            "session_id": attempt.session_id,
            "score": attempt.score,
            "messages": len(attempt.messages),
            "created_at": attempt.created_at
        } for attempt in user.attempts]
    } 