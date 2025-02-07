# src/routes/api.py

from fastapi import FastAPI, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from src.models.game import GameSession, SessionStatus
from src.models.database_models import DBSession, DBAttempt, DBMessage
from sqlalchemy.orm import Session
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
UTC = ZoneInfo("UTC")

app = FastAPI()

DEFAULT_ENTRY_FEE = 10.0  # Default WLDD tokens per game

# In-memory storage for MVP
current_session: Optional[GameSession] = None
attempts: dict[UUID, GameAttempt] = {}  # Store attempts by ID

# Response Models
class MessageResponse(BaseModel):
   content: str
   ai_response: str

class AttemptResponse(BaseModel):
   id: UUID
   session_id: UUID
   user_id: UUID
   messages: List[MessageResponse]
   is_winner: bool
   messages_remaining: int

class SessionResponse(BaseModel):
   id: UUID
   start_time: datetime
   end_time: datetime
   entry_fee: float
   total_pot: float
   status: str
   winning_attempts: List[UUID]

class UserResponse(BaseModel):
   id: UUID
   wldd_id: str
   stats: dict

# Game Session Management
@app.post("/sessions/create", response_model=SessionResponse)
async def create_session(entry_fee: float, db: Session = Depends(get_db)):
    # Check for active session
    active_session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    
    if active_session:
        raise HTTPException(status_code=400, detail="Active session already exists")
    
    start_time = datetime.now(UTC)
    end_time = start_time + timedelta(hours=1)
    
    db_session = DBSession(
        start_time=start_time,
        end_time=end_time,
        entry_fee=entry_fee,
        status=SessionStatus.ACTIVE.value
    )
    
    db.add(db_session)
    db.commit()
    db.refresh(db_session)
    
    return SessionResponse(
        id=db_session.id,
        start_time=db_session.start_time,
        end_time=db_session.end_time,
        entry_fee=db_session.entry_fee,
        total_pot=db_session.total_pot,
        status=db_session.status,
        winning_attempts=[]
    )

@app.get("/sessions/current", response_model=Optional[SessionResponse])
async def get_current_session():
    if not current_session:
        raise HTTPException(status_code=404, detail="No session found")
        
    now = datetime.now(UTC)
    
    # Update session status if it's ended
    if current_session.status == SessionStatus.ACTIVE and now > current_session.end_time:
        current_session.status = SessionStatus.COMPLETED
    
    return current_session

@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID):
   pass

@app.put("/sessions/{session_id}/end", response_model=SessionResponse)
async def end_session(session_id: UUID):
   pass

# Game Attempts
@app.post("/attempts/create", response_model=AttemptResponse)
async def create_attempt(user_id: UUID):
    if not current_session or current_session.status != SessionStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="No active session")
        
    # Check if user already has attempt in this session
    user_attempts = [a for a in attempts.values() 
                    if a.user_id == user_id and a.session_id == current_session.id]
    if user_attempts:
        raise HTTPException(status_code=400, detail="User already has attempt in this session")
    
    new_attempt = GameAttempt(
        id=uuid4(),
        session_id=current_session.id,
        user_id=user_id,
        messages=[],
        messages_remaining=5
    )
    
    attempts[new_attempt.id] = new_attempt
    current_session.attempts.append(new_attempt.id)
    
    return new_attempt

@app.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(attempt_id: UUID):
   pass

@app.post("/attempts/{attempt_id}/message", response_model=MessageResponse)
async def submit_message(attempt_id: UUID, message: str):
   pass

# User Management
@app.post("/users/create", response_model=UserResponse)
async def create_user():
   pass

@app.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID):
   pass

@app.get("/users/{user_id}/stats")
async def get_user_stats(user_id: UUID):
   pass

@app.get("/users/{user_id}/attempts", response_model=List[AttemptResponse])
async def get_user_attempts(user_id: UUID):
   pass

# Admin/System
@app.get("/sessions/stats")
async def get_session_stats():
   pass

@app.put("/sessions/{session_id}/verify")
async def verify_session(session_id: UUID):
   pass

# Status/Health
@app.get("/health")
async def health_check():
   pass

@app.get("/status")
async def system_status():
   pass