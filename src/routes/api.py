# src/routes/api.py

from dotenv import load_dotenv
load_dotenv()  # Add this line before any other imports

from fastapi import FastAPI, HTTPException, Depends
from typing import List, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from src.models.game import GameSession, SessionStatus, GameAttempt, Message
from src.models.database_models import DBSession, DBAttempt, DBMessage, DBUser
from sqlalchemy.orm import Session
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from src.services.score import get_score_service
from src.services.llm_service import LLMService
from src.database import engine, get_db, get_llm_service
from src.services.conversation import ConversationManager
from src.services.exceptions import LLMServiceError
from fastapi.middleware.cors import CORSMiddleware
from src.routes.admin import router as admin_router
from src.routes.admin_ui import router as admin_ui_router
from fastapi.templating import Jinja2Templates
from fastapi import BackgroundTasks

UTC = ZoneInfo("UTC")

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Local development
        "https://your-frontend-domain.com",  # Production frontend
        "*"  # Or temporarily allow all origins while testing
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include the admin routers
app.include_router(admin_router)
app.include_router(admin_ui_router)

DEFAULT_ENTRY_FEE = 10.0  # Default WLDD tokens per game

class CreateUserRequest(BaseModel):
    wldd_id: str

class MessageRequest(BaseModel):
    content: str

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
   total_pot: float
   
class SessionResponse(BaseModel):
   id: UUID
   start_time: datetime
   end_time: datetime
   entry_fee: float
   total_pot: float
   status: str
   winning_attempts: List[UUID]

# At the top of api.py with other models
class UserResponse(BaseModel):
    id: UUID
    wldd_id: str
    stats: dict

    class Config:
        from_attributes = True  # Add this for SQLAlchemy model compatibility

# Modify session creation to be more explicit about timing
@app.post("/sessions/create", response_model=SessionResponse)
async def create_session(
    entry_fee: float, 
    duration_hours: int = 1,
    db: Session = Depends(get_db)
):
    try:
        # Check for active session with row lock
        active_session = db.query(DBSession).with_for_update().filter(
            DBSession.status == SessionStatus.ACTIVE.value
        ).first()
        
        if active_session:
            raise HTTPException(status_code=400, detail="Active session already exists")
        
        start_time = datetime.now(UTC)
        end_time = start_time + timedelta(hours=duration_hours)
        
        print(f"Session timing: Start={start_time.isoformat()}, End={end_time.isoformat()}")
        
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
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions/current", response_model=Optional[SessionResponse])
async def get_current_session(db: Session = Depends(get_db)):
    session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    
    if not session:
        raise HTTPException(status_code=404, detail="No active session found")
    
    now = datetime.now(UTC)
    
    # Add logging to debug session timing
    print(f"Checking session {session.id}")
    print(f"Current time: {now}")
    print(f"End time: {session.end_time}")
    
    if now > session.end_time:
        print(f"Marking session {session.id} as completed")
        session.status = SessionStatus.COMPLETED.value
        db.commit()
    
    return SessionResponse(
        id=session.id,
        start_time=session.start_time,
        end_time=session.end_time,
        entry_fee=session.entry_fee,
        total_pot=session.total_pot,
        status=session.status,
        winning_attempts=[attempt.id for attempt in session.attempts if attempt.score > 7.0]
    )

@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID):
   pass

@app.put("/sessions/{session_id}/end", response_model=SessionResponse)
async def end_session(session_id: UUID, db: Session = Depends(get_db)):
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # Get all attempts with scores above threshold (you define threshold)
    winning_attempts = db.query(DBAttempt).filter(
        DBAttempt.session_id == session_id,
        DBAttempt.score > 0  # Or whatever threshold you want
    ).order_by(DBAttempt.score.desc()).all()
    
    if winning_attempts:
        # Calculate weighted distribution based on scores
        total_score = sum(attempt.score for attempt in winning_attempts)
        pot = session.total_pot
        
        for attempt in winning_attempts:
            # Each winner gets proportion of pot based on their score
            share = (attempt.score / total_score) * pot
            # Update user stats/balance here
            
    session.status = SessionStatus.COMPLETED
    db.commit()
    
    return SessionResponse(
        id=session.id,
        start_time=session.start_time,
        end_time=session.end_time,
        entry_fee=session.entry_fee,
        total_pot=session.total_pot,
        status=session.status,
        winning_attempts=[attempt.id for attempt in winning_attempts]
    )

# Game Attempts
@app.post("/attempts/create", response_model=AttemptResponse)
async def create_attempt(user_id: UUID, db: Session = Depends(get_db)):
    # Get active session from database instead of memory
    active_session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    
    if not active_session:
        raise HTTPException(status_code=400, detail="No active session")
        
    # Check if user already has attempt in this session
    existing_attempt = db.query(DBAttempt).filter(
        DBAttempt.user_id == user_id,
        DBAttempt.session_id == active_session.id
    ).first()
    
    if existing_attempt:
        raise HTTPException(status_code=400, detail="User already has attempt in this session")
    
    new_attempt = DBAttempt(
        id=uuid4(),
        session_id=active_session.id,
        user_id=user_id,
    )
    
    db.add(new_attempt)
    active_session.total_pot += active_session.entry_fee
    db.commit()
    db.refresh(new_attempt)
    
    return AttemptResponse(
        id=new_attempt.id,
        session_id=new_attempt.session_id,
        user_id=new_attempt.user_id,
        messages=[],
        is_winner=False,
        messages_remaining=new_attempt.messages_remaining,
        total_pot=active_session.total_pot
    )

@app.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(attempt_id: UUID, db: Session = Depends(get_db)):
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    return AttemptResponse(
        id=attempt.id,
        session_id=attempt.session_id,
        user_id=attempt.user_id,
        messages=[
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in attempt.messages
        ],
        is_winner=attempt.score > 7.0,  # Using same threshold as elsewhere
        messages_remaining=attempt.messages_remaining
    )

@app.post("/attempts/{attempt_id}/score")
async def score_attempt(
    attempt_id: UUID,
    score: float,
    db: Session = Depends(get_db),
    score_service = Depends(get_score_service)
):
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
        
    attempt.score = score
    db.commit()
    
    return {"attempt_id": attempt_id, "score": score}

@app.post("/attempts/{attempt_id}/message", response_model=MessageResponse)
async def submit_message(
    attempt_id: UUID,
    message: MessageRequest,
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service),
):
    conversation_manager = ConversationManager(llm_service, db)
    try:
        message_result = await conversation_manager.process_attempt_message(
            attempt_id,
            message.content
        )
        
        return MessageResponse(
            content=message_result.content,
            ai_response=message_result.ai_response
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LLMServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.post("/users/create", response_model=UserResponse)
async def create_user(request: CreateUserRequest, db: Session = Depends(get_db)):
    """Create a new user with WLDD wallet ID"""
    
    # Validate if user with WLDD ID already exists
    existing_user = db.query(DBUser).filter(DBUser.wldd_id == request.wldd_id).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this WLDD ID already exists")
    
    new_user = DBUser(
        id=uuid4(),
        wldd_id=request.wldd_id,
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC)
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return UserResponse(
        id=new_user.id,
        wldd_id=new_user.wldd_id,
        stats=new_user.get_stats()
    )

@app.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, db: Session = Depends(get_db)):
    """Get user details by ID"""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
        id=user.id,
        wldd_id=user.wldd_id,
        stats=user.get_stats()
    )

@app.get("/users/{user_id}/stats")
async def get_user_stats(user_id: UUID, db: Session = Depends(get_db)):
    """Get detailed user statistics"""
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get all user's attempts
    attempts = db.query(DBAttempt).filter(DBAttempt.user_id == user_id).all()
    
    # Count total messages using a subquery for efficiency
    total_messages = db.query(DBMessage)\
        .join(DBAttempt)\
        .filter(DBAttempt.user_id == user_id)\
        .count()
    
    stats = {
        "total_games": len(attempts),
        "total_wins": len([a for a in attempts if a.score > 7.0]),  # Example threshold
        "average_score": sum(a.score for a in attempts) / len(attempts) if attempts else 0,
        "total_messages": total_messages,
        "total_earnings": sum(a.earnings for a in attempts if a.earnings)
    }
    
    return stats

@app.get("/users/{user_id}/attempts", response_model=List[AttemptResponse])
async def get_user_attempts(
    user_id: UUID, 
    limit: int = 10, 
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Get user's game attempts with pagination"""
    attempts = db.query(DBAttempt)\
        .filter(DBAttempt.user_id == user_id)\
        .order_by(DBAttempt.created_at.desc())\
        .offset(offset)\
        .limit(limit)\
        .all()
    
    return [
        AttemptResponse(
            id=attempt.id,
            session_id=attempt.session_id,
            user_id=attempt.user_id,
            messages=[
                MessageResponse(
                    content=msg.content,
                    ai_response=msg.ai_response
                ) for msg in attempt.messages
            ],
            is_winner=attempt.score > 7.0,  # Example threshold
            messages_remaining=attempt.messages_remaining
        ) for attempt in attempts
    ]

# Admin/System Routes

@app.get("/sessions/stats")
async def get_session_stats(db: Session = Depends(get_db)):
    """Get global session statistics"""
    sessions = db.query(DBSession).all()
    
    stats = {
        "total_sessions": len(sessions),
        "total_active_sessions": len([s for s in sessions if s.status == SessionStatus.ACTIVE]),
        "total_completed_sessions": len([s for s in sessions if s.status == SessionStatus.COMPLETED]),
        "total_pot_distributed": sum(s.total_pot for s in sessions if s.status == SessionStatus.COMPLETED),
        "average_pot_size": sum(s.total_pot for s in sessions) / len(sessions) if sessions else 0,
        "total_attempts": sum(len(s.attempts) for s in sessions),
        "average_attempts_per_session": sum(len(s.attempts) for s in sessions) / len(sessions) if sessions else 0
    }
    
    return stats

@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID, db: Session = Depends(get_db)):
    """Get specific session details"""
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    return SessionResponse(
        id=session.id,
        start_time=session.start_time,
        end_time=session.end_time,
        entry_fee=session.entry_fee,
        total_pot=session.total_pot,
        status=session.status,
        winning_attempts=[attempt.id for attempt in session.attempts if attempt.score > 7.0]
    )

@app.put("/sessions/{session_id}/verify")
async def verify_session(
    session_id: UUID, 
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service)
):
    """Verify session results and recalculate scores if needed"""
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Recalculate scores for all attempts
    for attempt in session.attempts:
        score = await llm_service.score_conversation(attempt.messages)
        attempt.score = score
    
    db.commit()
    
    return {"message": "Session verified", "session_id": session_id}

@app.post("/attempts/{attempt_id}/force-score")
async def force_score_attempt(
    attempt_id: UUID,
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service)
):
    """Force scoring of an attempt (admin endpoint)"""
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    # Only allow scoring if all messages have been used
    if attempt.messages_remaining > 0:
        raise HTTPException(
            status_code=400, 
            detail="Cannot score attempt until all messages are used"
        )
    
    try:
        score = await llm_service.score_conversation(
            [Message(content=msg.content, timestamp=msg.timestamp)
             for msg in attempt.messages]
        )
        attempt.score = score
        db.commit()
        return {"attempt_id": attempt_id, "score": score}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scoring failed: {str(e)}")

# Status/Health Routes

@app.get("/health")
async def health_check():
    """Basic health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC)
    }

@app.get("/status")
async def system_status(db: Session = Depends(get_db)):
    """Detailed system status"""
    try:
        # Test DB connection
        db.execute("SELECT 1")
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    try:
        # Test LLM service
        llm_service = get_llm_service()
        llm_status = "available"
    except Exception as e:
        llm_status = f"error: {str(e)}"
    
    return {
        "database": db_status,
        "llm_service": llm_status,
        "api_version": "1.0.0",
        "timestamp": datetime.now(UTC),
        "active_sessions": db.query(DBSession)
            .filter(DBSession.status == SessionStatus.ACTIVE)
            .count()
    }