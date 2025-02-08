from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from src.database import get_db
from src.models.game import SessionStatus
from src.models.database_models import DBSession, DBAttempt, DBUser, DBMessage
from src.services.llm_service import LLMService
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import UUID
from tabulate import tabulate
import os

router = APIRouter(prefix="/admin")

API_KEY_NAME = "X-Admin-Key"
API_KEY = os.getenv("ADMIN_API_KEY")
api_key_header = APIKeyHeader(name=API_KEY_NAME)

UTC = ZoneInfo("UTC")

async def get_api_key(api_key_header: str = Security(api_key_header)):
    if not API_KEY or api_key_header != API_KEY:
        raise HTTPException(status_code=403, detail="Could not validate credentials")
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
        
        db.add(new_session)
        db.commit()
        db.refresh(new_session)
        
        return {
            "message": "Session created successfully",
            "session_id": new_session.id,
            "start_time": start_time,
            "end_time": end_time,
            "entry_fee": entry_fee
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/sessions/{session_id}/end")
async def admin_end_session(
    session_id: UUID,
    api_key: str = Depends(get_api_key),
    db = Depends(get_db)
):
    """End a specific session"""
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    session.status = SessionStatus.COMPLETED.value
    db.commit()
    
    winners = [attempt for attempt in session.attempts if attempt.score and attempt.score > 7.0]
    
    return {
        "message": "Session ended",
        "session_id": session_id,
        "final_pot": session.total_pot,
        "total_attempts": len(session.attempts),
        "winning_attempts": len(winners)
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
        winners = len([a for a in session.attempts if a.score and a.score > 7.0])
        session_data.append({
            "id": str(session.id),
            "status": session.status,
            "start_time": session.start_time.strftime("%Y-%m-%d %H:%M"),
            "end_time": session.end_time.strftime("%Y-%m-%d %H:%M"),
            "entry_fee": session.entry_fee,
            "total_pot": session.total_pot,
            "attempts": len(session.attempts),
            "winners": winners
        })
    
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
        user = db.query(DBUser).filter(DBUser.id == attempt.user_id).first()
        messages = [
            {
                "content": msg.content,
                "ai_response": msg.ai_response
            } for msg in attempt.messages
        ]
        attempts.append({
            "id": str(attempt.id),
            "user": user.wldd_id if user else "Unknown",
            "score": attempt.score or "Not scored",
            "message_count": len(messages),
            "messages": messages,
            "remaining": attempt.messages_remaining,
            "is_winner": bool(attempt.score and attempt.score > 7.0)
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