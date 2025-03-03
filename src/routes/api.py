# src/routes/api.py

from dotenv import load_dotenv
load_dotenv()  # Add this line before any other imports

from fastapi import FastAPI, HTTPException, Depends, Request
from typing import List, Optional
from pydantic import BaseModel, Field, validator
from datetime import datetime, timedelta, date
from src.models.game import GameSession, SessionStatus, GameAttempt, Message
from src.models.database_models import DBSession, DBAttempt, DBMessage, DBUser, DBVerification, DBPayment
from sqlalchemy.orm import Session
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from src.services.score import get_score_service
from src.services.llm_service import LLMService
from src.database import engine, get_db, get_llm_service
from src.services.conversation import ConversationManager
from src.services.exceptions import LLMServiceError
from fastapi.middleware.cors import CORSMiddleware
from src.routes.admin import (
    router as admin_router, 
    get_api_key,
    admin_create_session,
    admin_end_session
)
from src.routes.admin_ui import router as admin_ui_router
from fastapi.templating import Jinja2Templates
from fastapi import BackgroundTasks
import time
import httpx
import os
from sqlalchemy import and_
import secrets
import json
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from src.config.logging_config import setup_logging
import asyncio

# Set up logging first, before any other imports
logger = setup_logging()

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
    language: Optional[str] = Field(default="ENGLISH")

class MessageRequest(BaseModel):
    content: str

class MessageResponse(BaseModel):
   content: str
   ai_response: str

class AttemptResponse(BaseModel):
   id: UUID
   session_id: UUID
   wldd_id: str
   messages: List[MessageResponse]
   score: Optional[float]
   messages_remaining: int
   total_pot: float
   earnings: Optional[float] = None
   is_free_attempt: bool = False
   
class SessionResponse(BaseModel):
   id: UUID
   start_time: datetime
   end_time: datetime
   entry_fee: float
   total_pot: float
   status: str
   attempts: List[dict]
   winning_conversation: Optional[List[MessageResponse]] = None

   @validator('entry_fee', 'total_pot')
   def round_amounts(cls, v):
       return round(v, 2) if v is not None else v

# At the top of api.py with other models
class UserResponse(BaseModel):
    wldd_id: str
    stats: dict
    language: str  # Update language field

    class Config:
        from_attributes = True

class VerifyRequest(BaseModel):
    nullifier_hash: str
    merkle_root: str
    proof: str
    verification_level: str
    action: str
    name: str = Field(default="Anonymous User")  # Add name field with default
    language: str = Field(default="english")  # Add language field with default

class PaymentInitResponse(BaseModel):
    reference: str
    recipient: Optional[str]
    amount: float

    @validator('amount')
    def round_amount(cls, v):
        return round(v, 2) if v is not None else v

class PaymentConfirmRequest(BaseModel):
    reference: str
    payload: dict  # This will hold the MiniAppPaymentSuccessPayload

class WorldIDCredentials(BaseModel):
    nullifier_hash: str
    merkle_root: str
    proof: str
    verification_level: str

# Add this with other models at the top
class CreateAttemptRequest(BaseModel):
    payment_reference: str

class UpdateLanguageRequest(BaseModel):
    language: str = Field(description="User's preferred language (e.g. 'english' or 'spanish')")

# Add this with other models at the top
class AdminAttemptFilters(BaseModel):
    status: Optional[str] = None  # 'completed', 'in_progress'
    score_min: Optional[float] = None
    score_max: Optional[float] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    wldd_id: Optional[str] = None

# Move these helper functions before the routes
async def verify_world_id_credentials(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[WorldIDCredentials]:
    """Verify World ID credentials and check against stored verifications"""    
    logger.info("=== Starting verify_world_id_credentials ===")
    logger.info(f"Headers: {request.headers}")
    
    credentials = request.headers.get('X-WorldID-Credentials')
    if not credentials:
        logger.info("No credentials found in header")
        return None
        
    creds = json.loads(credentials)
    logger.info(f"Received credentials for nullifier_hash: {creds['nullifier_hash']}")
    
    parsed_creds = WorldIDCredentials(
        nullifier_hash=creds["nullifier_hash"],
        merkle_root=creds["merkle_root"],
        proof=creds["proof"],
        verification_level=creds["verification_level"]
    )
    
    verification = db.query(DBVerification).filter(
        DBVerification.nullifier_hash == parsed_creds.nullifier_hash
    ).first()
    
    if not verification:
        logger.error(f"No verification found for nullifier_hash: {parsed_creds.nullifier_hash}")
        return None
    
    logger.info(f"Found verification for nullifier_hash: {parsed_creds.nullifier_hash}")
        
    # Update last_active without changing language
    user = db.query(DBUser).filter(
        DBUser.wldd_id == parsed_creds.nullifier_hash
    ).first()
    if user:
        logger.info(f"Found user with wldd_id: {user.wldd_id}")
        user.last_active = datetime.now(UTC)
        db.commit()
    else:
        logger.error(f"No user found with wldd_id: {parsed_creds.nullifier_hash}")
        
    return parsed_creds

@app.get("/userinfo/has_free_attempt", response_model=bool)
async def has_free_attempt(
    db: Session = Depends(get_db),
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
):
    """Check if user has unused free attempt"""
    logger.info("=== Starting has_free_attempt route ===")
    logger.info(f"Received credentials: {credentials}")
    
    if not credentials:
        logger.info("No credentials provided to has_free_attempt")
        raise HTTPException(status_code=401, detail="Unauthorized")

    wldd_id = credentials.nullifier_hash
    logger.info(f"Checking free attempt for wldd_id: {wldd_id}")
    
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        logger.info(f"User not found in has_free_attempt for wldd_id: {wldd_id}")
        raise HTTPException(status_code=404, detail="User not found")

    logger.info(f"Found user in has_free_attempt, used_free_attempt: {user.used_free_attempt}")
    return not user.used_free_attempt

# Modify session creation to be more explicit about timing
@app.post("/sessions/create", response_model=SessionResponse)
async def create_session(
    entry_fee: float,
    duration_hours: int = 24,
    api_key: str = Depends(get_api_key),  # Add API key requirement
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
            attempts=[]
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions/current", response_model=Optional[SessionResponse])
async def get_current_session(db: Session = Depends(get_db)):
    """Get the current active session with retries if none exists"""
    max_retries = 3
    retry_delay = 2  # seconds
    
    for attempt in range(max_retries):
        session = db.query(DBSession).filter(
            DBSession.status == SessionStatus.ACTIVE.value
        ).first()
        
        if session:
            attempts = db.query(DBAttempt).filter(
                DBAttempt.session_id == session.id
            ).all()
            
            return SessionResponse(
                id=session.id,
                start_time=session.start_time,
                end_time=session.end_time,
                entry_fee=session.entry_fee,
                total_pot=session.total_pot,
                status=session.status,
                attempts=[{
                    'id': attempt.id,
                    'score': attempt.score,
                    'earnings': attempt.earnings
                } for attempt in attempts]
            )
        
        # No session found, wait before retrying
        if attempt < max_retries - 1:  # Don't wait on last attempt
            print(f"No active session found, retrying in {retry_delay} seconds...")
            await asyncio.sleep(retry_delay)
            # Refresh DB session to avoid stale data
            db.close()
            db = next(get_db())
    
    # If we get here, we've exhausted all retries
    raise HTTPException(
        status_code=404, 
        detail="No active session found after retries. Please try again in a moment."
    )

@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID, db: Session = Depends(get_db)):
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    winning_conversation = None
    if session.winning_attempt:
        winning_conversation = [
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in session.winning_attempt.messages
        ]
    
    return SessionResponse(
        id=session.id,
        start_time=session.start_time,
        end_time=session.end_time,
        entry_fee=session.entry_fee,
        total_pot=session.total_pot,
        status=session.status,
        attempts=[{
            'id': attempt.id,
            'score': attempt.score,
            'earnings': attempt.earnings
        } for attempt in session.attempts if attempt.score is not None],
        winning_conversation=winning_conversation
    )

@app.put("/sessions/{session_id}/end", response_model=SessionResponse)
async def end_session(session_id: UUID, db: Session = Depends(get_db)):
    session = db.query(DBSession).filter(DBSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # Get all scored attempts for this session
    attempts = db.query(DBAttempt).filter(
        DBAttempt.session_id == session_id,
        DBAttempt.score.isnot(None)  # Only consider attempts that have been scored
    ).order_by(DBAttempt.score.desc()).all()
    
    winning_conversation = None
    
    if attempts:
        # Separate paid and free attempts
        paid_attempts = [a for a in attempts if not a.is_free_attempt]
        
        # Calculate total score across paid attempts only
        total_score = sum(attempt.score for attempt in paid_attempts)
        pot = session.total_pot
        
        # Distribute pot based on relative scores (paid attempts only)
        for attempt in attempts:
            if attempt.is_free_attempt:
                attempt.earnings = 0
            else:
                # Calculate proportional share of pot
                share = (attempt.score / total_score) * pot if total_score > 0 else 0
                attempt.earnings = share
        
        # Find highest scoring attempts (including free attempts)
        max_score = attempts[0].score  # We know attempts is sorted desc
        top_attempts = [a for a in attempts if a.score == max_score]
        
        # Randomly select one of the highest scoring attempts
        import random
        winning_attempt = random.choice(top_attempts)
        
        # Store the winning attempt in the session
        session.winning_attempt_id = winning_attempt.id
        
        # Prepare winning conversation for response
        winning_conversation = [
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in winning_attempt.messages
        ]
            
    session.status = SessionStatus.COMPLETED
    db.commit()
    
    return SessionResponse(
        id=session.id,
        start_time=session.start_time,
        end_time=session.end_time,
        entry_fee=session.entry_fee,
        total_pot=session.total_pot,
        status=session.status,
        attempts=[{
            'id': attempt.id,
            'score': attempt.score,
            'earnings': attempt.earnings,
            'is_free_attempt': attempt.is_free_attempt
        } for attempt in attempts],
        winning_conversation=winning_conversation,
        winning_attempt_was_free=session.winning_attempt.is_free_attempt if session.winning_attempt_id else None
    )

# Game Attempts
@app.post("/attempts/create", response_model=AttemptResponse)
async def create_attempt(
    request: CreateAttemptRequest,
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    """Create a new attempt"""
    print(f"Create attempt credentials: {credentials}")
    is_dev_mode = os.getenv("ENVIRONMENT") == "development"
    
    if credentials or not is_dev_mode:
        if not credentials:
            raise HTTPException(status_code=401, detail="World ID verification required")
    
    # Get wldd_id from credentials
    wldd_id = credentials.nullifier_hash if credentials else None
    
    # Get active session first
    active_session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    
    if not active_session:
        raise HTTPException(status_code=400, detail="No active session")
    
    # Get user by wldd_id
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Handle free attempt
    if request.payment_reference.startswith("free_attempt_"):
        if user.used_free_attempt:
            raise HTTPException(status_code=400, detail="Free attempt already used")
        
        # Mark free attempt as used
        user.used_free_attempt = True
        new_attempt = DBAttempt(
            id=uuid4(),
            session_id=active_session.id,
            wldd_id=wldd_id,
            is_free_attempt=True
        )
        db.add(new_attempt)
        db.commit()
        db.refresh(new_attempt)
        
        return AttemptResponse(
            id=new_attempt.id,
            session_id=new_attempt.session_id,
            wldd_id=new_attempt.wldd_id,
            messages=[],
            score=None,
            messages_remaining=new_attempt.messages_remaining,
            total_pot=active_session.total_pot,
            earnings=None,
            is_free_attempt=True
        )
            
    # Regular paid attempt flow
    if credentials:
        if not request.payment_reference:
            raise HTTPException(status_code=400, detail="Payment reference required")
            
        print(f"Looking for payment with reference: {request.payment_reference}")
        payment1 = db.query(DBPayment).filter(
            DBPayment.reference == request.payment_reference
        ).first()
        print(f"Payment info: {payment1.reference}, {payment1.wldd_id}, {payment1.status}, {payment1.consumed}, {payment1.amount_raw}")
        print(f"Required payment info: {request.payment_reference}, {wldd_id}, confirmed, false, {active_session.entry_fee_raw}")
        payment = db.query(DBPayment).with_for_update().filter(
            DBPayment.reference == request.payment_reference,
            DBPayment.wldd_id == wldd_id,
            DBPayment.status == "confirmed",
            DBPayment.consumed == False,
            DBPayment.amount_raw == active_session.entry_fee_raw  # Compare raw values
        ).first()
        print(f"Found payment: {payment}")
        if payment:
            print(f"Payment details: status={payment.status}, consumed={payment.consumed}, amount={payment.amount}, fee={active_session.entry_fee}")
        
        if not payment:
            raise HTTPException(
                status_code=400, 
                detail="Valid payment required. Payment must be confirmed, unused, and match current entry fee."
            )
            
        # Check if payment is recent (e.g., within last hour)
        payment_age = datetime.now(UTC) - payment.created_at
        if payment_age > timedelta(hours=1):
            raise HTTPException(
                status_code=400,
                detail="Payment has expired. Please make a new payment."
            )
    
    new_attempt = DBAttempt(
        id=uuid4(),
        session_id=active_session.id,
        wldd_id=wldd_id,
        is_free_attempt=False
    )
    
    db.add(new_attempt)
    active_session.total_pot += active_session.entry_fee
    
    if credentials or not is_dev_mode:
        # Mark payment as consumed
        payment.consumed = True
        payment.consumed_at = datetime.now(UTC)
        payment.consumed_by_attempt_id = new_attempt.id
    
    db.commit()
    db.refresh(new_attempt)
    
    return AttemptResponse(
        id=new_attempt.id,
        session_id=new_attempt.session_id,
        wldd_id=new_attempt.wldd_id,
        messages=[],
        score=None,
        messages_remaining=new_attempt.messages_remaining,
        total_pot=active_session.total_pot,
        earnings=None,
        is_free_attempt=False
    )

@app.get("/attempts/{attempt_id}", response_model=AttemptResponse)
async def get_attempt(
    attempt_id: UUID,
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="World ID verification required")
    
    wldd_id = credentials.nullifier_hash
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    # Verify ownership
    if attempt.wldd_id != wldd_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    return AttemptResponse(
        id=attempt.id,
        session_id=attempt.session_id,
        wldd_id=attempt.wldd_id,
        messages=[
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in attempt.messages
        ],
        score=attempt.score,
        messages_remaining=attempt.messages_remaining,
        total_pot=attempt.session.total_pot,
        earnings=attempt.earnings,
        is_free_attempt=attempt.is_free_attempt
    )

@app.post("/attempts/{attempt_id}/score", response_model=AttemptResponse)
async def score_attempt(
    attempt_id: UUID,
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="World ID verification required")
    
    wldd_id = credentials.nullifier_hash
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    # Verify ownership
    if attempt.wldd_id != wldd_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Score the attempt
    score, cost = await llm_service.score_conversation(
        [Message(content=msg.content, timestamp=msg.timestamp)
         for msg in attempt.messages]
    )
    attempt.score = score
    attempt.cost_to_run += cost
    db.commit()
    
    return AttemptResponse(
        id=attempt.id,
        session_id=attempt.session_id,
        wldd_id=attempt.wldd_id,
        messages=[
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in attempt.messages
        ],
        score=attempt.score,
        messages_remaining=attempt.messages_remaining,
        total_pot=attempt.total_pot,
        earnings=attempt.earnings,
        is_free_attempt=attempt.is_free_attempt
    )

@app.post("/attempts/{attempt_id}/message", response_model=MessageResponse)
async def submit_message(
    attempt_id: UUID,
    message: MessageRequest,
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service),
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="World ID verification required")

    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
        
    if attempt.messages_remaining <= 0:
        raise HTTPException(
            status_code=400, 
            detail="No messages remaining for this attempt. Purchase more messages to continue."
        )
    
    # Get user's name
    user = db.query(DBUser).filter(DBUser.wldd_id == attempt.wldd_id).first()
    print(f"User language: {user.language}")
    user_name = user.name if user else None
    
    conversation_manager = ConversationManager(llm_service, db)
    try:
        message_result = await conversation_manager.process_attempt_message(
            attempt_id,
            message.content,
            user_name
        )

        db.commit()
        
        return MessageResponse(
            content=message_result.content,
            ai_response=message_result.ai_response
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except LLMServiceError as e:
        raise HTTPException(status_code=503, detail=str(e))

@app.post("/users/create", response_model=UserResponse)
async def create_user(
    request: CreateUserRequest, 
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    """Create a new user with WLDD wallet ID"""
    is_dev_mode = os.getenv("ENVIRONMENT") == "development"
    
    # In production, require World ID verification
    if (credentials or not is_dev_mode) and not credentials:
        raise HTTPException(
            status_code=401, 
            detail="World ID verification required"
        )
    
    # Validate if user with WLDD ID already exists
    existing_user = db.query(DBUser).filter(
        DBUser.wldd_id == request.wldd_id
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=400, 
            detail="User with this WLDD ID already exists"
        )
    
    new_user = DBUser(
        wldd_id=request.wldd_id,
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
        language=request.language
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return UserResponse(
        wldd_id=new_user.wldd_id,
        stats=new_user.get_stats(),
        language=new_user.language
    )

@app.get("/userinfo/{wldd_id}", response_model=UserResponse)
async def get_user(wldd_id: str, db: Session = Depends(get_db)):
    """Get user details by WLDD ID"""
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
        wldd_id=user.wldd_id,
        stats=user.get_stats(),
        language=user.language
    )

@app.get("/userinfo/{wldd_id}/stats")
async def get_user_stats(wldd_id: str, db: Session = Depends(get_db)):
    """Get detailed user statistics"""
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Get all user's attempts
    attempts = db.query(DBAttempt).filter(DBAttempt.wldd_id == wldd_id).all()
    
    # Count total messages using a subquery for efficiency
    total_messages = db.query(DBMessage)\
        .join(DBAttempt)\
        .filter(DBAttempt.wldd_id == wldd_id)\
        .count()
    
    stats = {
        "total_games": len(attempts),
        "total_earnings": sum(a.earnings for a in attempts if a.earnings),
        "average_score": sum(a.score for a in attempts if a.score) / len([a for a in attempts if a.score]) if attempts else 0,
        "total_messages": total_messages,
        "best_score": max((a.score for a in attempts if a.score), default=0),
        "completed_sessions": len(set(a.session_id for a in attempts if a.session.status == SessionStatus.COMPLETED))
    }
    
    return stats

@app.get("/userinfo/{wldd_id}/attempts", response_model=List[AttemptResponse])
async def get_user_attempts(
    wldd_id: str, 
    limit: int = 10, 
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Get user's game attempts with pagination"""
    attempts = db.query(DBAttempt)\
        .filter(DBAttempt.wldd_id == wldd_id)\
        .order_by(DBAttempt.created_at.desc())\
        .offset(offset)\
        .limit(limit)\
        .all()
    
    return [AttemptResponse(
        id=attempt.id,
        session_id=attempt.session_id,
        wldd_id=attempt.wldd_id,
        messages=[
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in attempt.messages
        ],
        score=attempt.score,
        messages_remaining=attempt.messages_remaining,
        total_pot=attempt.total_pot,
        earnings=attempt.earnings,
        is_free_attempt=attempt.is_free_attempt
    ) for attempt in attempts]

@app.post("/users/language", response_model=UserResponse)
async def update_language(
    request: UpdateLanguageRequest,
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    """Update a user's preferred language."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")
        
    # Get user by nullifier hash
    user = db.query(DBUser).filter(
        DBUser.wldd_id == credentials.nullifier_hash
    ).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Update language
    user.language = request.language.lower()
    user.last_active = datetime.now(UTC)
    
    try:
        db.commit()
        return UserResponse(
            wldd_id=user.wldd_id,
            stats=user.get_stats(),
            language=user.language
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update language: {str(e)}")

# Admin/System Routes

# Add at the top with other environment variables
ADMIN_NULLIFIER_HASHES = os.getenv("ADMIN_NULLIFIER_HASHES", "").split(",")
print(f"Loaded admin hashes: {ADMIN_NULLIFIER_HASHES}")  # Log on startup

@app.get("/api/admin/unpaid_attempts")
async def get_unpaid_attempts(db: Session = Depends(get_db)):
    """Get all unpaid attempts with earnings"""
    attempts = db.query(DBAttempt).join(DBUser).filter(
        DBAttempt.earnings_raw > 0,
        DBAttempt.paid == False,
        DBUser.wallet_address.isnot(None)
    ).all()
    
    return [{
        'attempt_id': attempt.id,
        'session_id': attempt.session_id,
        'wldd_id': attempt.wldd_id,
        'wallet_address': attempt.user.wallet_address,
        'earnings_raw': attempt.earnings_raw,
        'created_at': attempt.created_at
    } for attempt in attempts]

@app.post("/api/admin/attempts/{attempt_id}/mark_paid")
async def mark_attempt_paid(attempt_id: UUID, db: Session = Depends(get_db), credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials)):
    """Mark an attempt as paid"""
    if not credentials:
        logger.error("No credentials provided to has_free_attempt")
        raise HTTPException(status_code=401, detail="Unauthorized")

    wldd_id = credentials.nullifier_hash
    logger.info(f"Marking attempt as paid on behalf of: {wldd_id}")
    if wldd_id not in ADMIN_NULLIFIER_HASHES:
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    attempt.paid = True
    db.commit()
    return {"success": True}

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
        attempts=[{
            'id': attempt.id,
            'score': attempt.score,
            'earnings': attempt.earnings
        } for attempt in session.attempts if attempt.score is not None]
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
        score, cost = await llm_service.score_conversation(attempt.messages)
        attempt.score = score
        attempt.cost_to_run += cost
    
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
    
    
    try:
        score, cost = await llm_service.score_conversation(
            [Message(content=msg.content, timestamp=msg.timestamp)
             for msg in attempt.messages]
        )
        attempt.score = score
        attempt.cost_to_run += cost
        db.commit()
        return {"attempt_id": attempt_id, "score": score}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scoring failed: {str(e)}")

@app.get("/admin/attempts")
async def get_all_attempts(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    filters: AdminAttemptFilters = None,
    db: Session = Depends(get_db)
):
    """Get all attempts with pagination and filtering for admin panel"""
    # Verify admin access
    credentials = await verify_world_id_credentials(request, db)
    if credentials.nullifier_hash not in ADMIN_NULLIFIER_HASHES:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    query = db.query(DBAttempt)
    
    if filters:
        if filters.status == 'completed':
            query = query.filter(DBAttempt.messages_remaining == 0)
        elif filters.status == 'in_progress':
            query = query.filter(DBAttempt.messages_remaining > 0)
            
        if filters.score_min is not None:
            query = query.filter(DBAttempt.score >= filters.score_min)
        if filters.score_max is not None:
            query = query.filter(DBAttempt.score <= filters.score_max)
            
        if filters.date_from:
            query = query.filter(DBAttempt.created_at >= filters.date_from)
        if filters.date_to:
            query = query.filter(DBAttempt.created_at <= filters.date_to)
            
        if filters.wldd_id:
            query = query.filter(DBAttempt.wldd_id == filters.wldd_id)
    
    # Get total count for pagination
    total_count = query.count()
    
    # Add sorting and pagination
    query = query.order_by(DBAttempt.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    attempts = query.all()
    
    # Load messages for each attempt
    for attempt in attempts:
        attempt.messages = db.query(DBMessage).filter(
            DBMessage.attempt_id == attempt.id
        ).order_by(DBMessage.id).all()
    
    return {
        "attempts": attempts,
        "total": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": (total_count + page_size - 1) // page_size
    }

@app.post("/verify", response_model=dict)
async def verify_world_id(request: VerifyRequest, db: Session = Depends(get_db)):
    try:
        print(f"Verifying nullifier_hash: {request.nullifier_hash}")
        print(f"Is admin hash? {request.nullifier_hash in ADMIN_NULLIFIER_HASHES}")
        print(f"Language: {request.language}")
        
        # First check if user exists (using nullifier_hash as ID)
        user = db.query(DBUser).filter(
            DBUser.wldd_id == request.nullifier_hash
        ).first()
        
        # Check if user has already verified today BEFORE trying World ID
        today = date.today()
        existing_verification = db.query(DBVerification).filter(
            and_(
                DBVerification.nullifier_hash == request.nullifier_hash,
            )
        ).first()
        
        if existing_verification:
            # If user doesn't exist but has verification, create user
            if not user:
                user = DBUser(
                    wldd_id=request.nullifier_hash,
                    created_at=datetime.now(UTC),
                    last_active=datetime.now(UTC),
                    name=request.name,
                    language=request.language.lower()
                )
                db.add(user)
                db.commit()
            else:
                user.last_active = datetime.now(UTC)
                user.name = request.name
                # Always update language if provided in request
                if request.language:
                    print(f"Updating language to {request.language.lower()}")
                    user.language = request.language.lower()
                db.commit()

            # Return success with existing verification
            is_admin = request.nullifier_hash in ADMIN_NULLIFIER_HASHES
            redirect_url = "/admin/payments" if is_admin else "/game"
            print(f"Verification successful. Admin: {is_admin}, Redirect: {redirect_url}")
            
            return {
                "success": True,
                "verification": {
                    "nullifier_hash": existing_verification.nullifier_hash,
                    "merkle_root": existing_verification.merkle_root,
                    "action": existing_verification.action
                },
                "user": {
                    "wldd_id": user.wldd_id,
                    "language": user.language  # Return language in response
                },
                "is_admin": is_admin,
                "redirect_url": redirect_url
            }

        # Only call World ID API if we don't have a valid verification
        verify_data = {
            "nullifier_hash": request.nullifier_hash,
            "merkle_root": request.merkle_root,
            "proof": request.proof,
            "verification_level": request.verification_level,
            "action": request.action
        }
        app_id = os.getenv("WORLD_ID_APP_ID")
        print(f"Verifying with app_id: {app_id}")
        print(f"Request data: {verify_data}")
        
        async with httpx.AsyncClient() as client:
            verify_url = f"https://developer.worldcoin.org/api/v2/verify/{app_id}"
            print(f"Making request to: {verify_url}")
            
            response = await client.post(verify_url, json=verify_data)
            print(f"World ID API response status: {response.status_code}")
            print(f"World ID API response body: {response.text}")
            
            if response.status_code != 200:
                error_detail = response.json() if response.text else "Unknown error"
                raise HTTPException(
                    status_code=400,
                    detail=f"World ID verification failed: {error_detail}"
                )
            
            verify_response = response.json()
            
            # Store new verification
            verification = DBVerification(
                nullifier_hash=request.nullifier_hash,
                merkle_root=request.merkle_root,
                action=request.action,
                created_at=datetime.now(UTC)
            )
            
            db.add(verification)
            db.commit()
            
            # After successful verification, create user if they don't exist
            user = db.query(DBUser).filter(
                DBUser.wldd_id == request.nullifier_hash
            ).first()
            
            if not user:
                user = DBUser(
                    wldd_id=request.nullifier_hash,
                    created_at=datetime.now(UTC),
                    last_active=datetime.now(UTC),
                    name=request.name,
                    language=request.language.lower()
                )
                db.add(user)
                db.commit()
            else:
                user.name = request.name
                # Always update language if provided in request
                if request.language:
                    user.language = request.language.lower()
                db.commit()
            
            is_admin = request.nullifier_hash in ADMIN_NULLIFIER_HASHES
            redirect_url = "/admin/payments" if is_admin else "/game"
            print(f"Verification successful. Admin: {is_admin}, Redirect: {redirect_url}")
            
            return {
                "success": True,
                "verification": verify_response,
                "user": {
                    "wldd_id": user.wldd_id,
                    "language": user.language  # Return language in response
                },
                "is_admin": is_admin,
                "redirect_url": redirect_url
            }
            
    except httpx.RequestError:
        raise HTTPException(
            status_code=500,
            detail="Failed to verify with World ID"
        )

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

@app.post("/payments/initiate", response_model=PaymentInitResponse)
async def initiate_payment(
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    """Initialize a payment for game attempt"""
    print(f"Initiate payment")  # Debug log
    if not credentials:
        print("No credentials in initiate_payment")  # Debug log
        raise HTTPException(status_code=401, detail="World ID verification required")

    # Get user from credentials
    wldd_id = credentials.nullifier_hash
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if user has free attempt available
    if not user.used_free_attempt:
        # Generate unique reference for this user's free attempt
        free_attempt_ref = f"free_attempt_{wldd_id[:8]}"
        payment = DBPayment(
            reference=free_attempt_ref,
            status="pending",
            amount_raw=0,
            wldd_id=wldd_id
        )
        db.add(payment)
        db.commit()
        return {"reference": free_attempt_ref, "amount": 0, "recipient": ""}

    # Regular payment flow
    # Get current active session to get entry fee
    active_session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    
    if not active_session:
        raise HTTPException(status_code=400, detail="No active session found")
    
    # Generate unique reference
    reference = secrets.token_hex(16)
    
    # Store payment reference
    payment = DBPayment(
        reference=reference,
        wldd_id=wldd_id,
        created_at=datetime.now(UTC)
    )
    db.add(payment)
    db.commit()
    
    return PaymentInitResponse(
        reference=reference,
        recipient=os.getenv("PAYMENT_RECIPIENT_ADDRESS"),
        amount=active_session.entry_fee  # Use current session's entry fee
    )

@app.post("/payments/confirm")
async def confirm_payment(request: PaymentConfirmRequest, db: Session = Depends(get_db)):
    """Confirm a payment using World ID API"""
    payment = db.query(DBPayment).filter(
        DBPayment.reference == request.reference
    ).first()
    
    if not payment:
        return {"success": False, "error": "Payment not found"}
    
    try:
        # Handle free attempt confirmation
        if request.reference.startswith("free_attempt_"):
            if (request.payload.get("status") == "success" and 
                request.payload.get("transaction_id") == "free_attempt"):
                payment.status = "confirmed"
                payment.transaction_id = "free_attempt"
                db.commit()
                return {"success": True}
            return {"success": False, "error": "Invalid free attempt confirmation"}
            
        # Regular payment verification with World ID API
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://developer.worldcoin.org/api/v2/minikit/transaction/{request.payload['transaction_id']}",
                params={
                    "app_id": os.getenv("WORLD_ID_APP_ID"),
                    "type": "payment"  # Add this - required param
                },
                headers={
                    "Authorization": f"Bearer {os.getenv('DEV_PORTAL_API_KEY')}"
                }
            )
            
            transaction = response.json()
            print(f"Transaction response: {transaction}")  # Debug log
            
            if (transaction.get("reference") == request.reference and 
                transaction.get("transaction_status") != "failed"):
                payment.status = "confirmed"
                payment.transaction_id = request.payload["transaction_id"]
                # Convert from 18 decimals (WLD standard) to our 6 decimal standard
                wld_amount_raw = int(transaction.get("inputTokenAmount", "0"))
                payment.amount_raw = wld_amount_raw // 10**12  # Divide by 10^12 to convert from 18 to 6 decimals
                print(f"Converting payment amount from {wld_amount_raw} (18 decimals) to {payment.amount_raw} (6 decimals)")
                
                # Update user's wallet address if available
                if transaction.get("fromWalletAddress"):
                    user = db.query(DBUser).filter(DBUser.wldd_id == payment.wldd_id).first()
                    if user:
                        user.wallet_address = transaction["fromWalletAddress"]
                
                db.commit()
                return {"success": True}
            
            payment.status = "failed"
            db.commit()
            return {"success": False, "error": "Payment failed"}
            
    except Exception as e:
        print(f"Payment confirmation failed: {e}")
        return {"success": False, "error": str(e)}

@app.post("/api/payments/{reference}/confirm")
async def admin_confirm_payment(
    reference: str,
    payload: dict,
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    """Log admin payment confirmation"""
    # Verify admin status using nullifier hash
    if not credentials or credentials.nullifier_hash not in ADMIN_NULLIFIER_HASHES:
        raise HTTPException(
            status_code=403,
            detail="Not authorized as admin"
        )
    
    # Find existing payment by reference
    payment = db.query(DBPayment).filter(DBPayment.reference == reference).first()
    if not payment:
        raise HTTPException(
            status_code=404,
            detail=f"No payment found with reference: {reference}"
        )
    
    print(f"Admin payment confirmed - Reference: {reference}, Transaction: {payload.get('transaction_id')}")
    
    # Update the payment status and transaction details
    payment.status = 'confirmed'
    payment.transaction_id = payload.get('transaction_id')
    payment.consumed = True
    payment.consumed_at = datetime.now(UTC)
    
    db.commit()
    
    return {"success": True}

# Create scheduler
scheduler = AsyncIOScheduler()

async def check_and_end_sessions():
    """Check for expired sessions and end them, start new ones if needed"""
    db = next(get_db())
    try:
        # First check for expired sessions
        expired_session = db.query(DBSession).filter(
            DBSession.status == SessionStatus.ACTIVE.value,
            DBSession.end_time <= datetime.now(UTC)
        ).first()
        
        if expired_session:
            print(f"Found expired session {expired_session.id}, ending it...")
            await admin_end_session(
                session_id=expired_session.id,
                api_key=os.getenv("ADMIN_API_KEY"),
                db=db
            )
        
        # Then check if we need to start a new session
        active_session = db.query(DBSession).filter(
            DBSession.status == SessionStatus.ACTIVE.value
        ).first()
        
        if not active_session:
            print("No active session found, creating new one...")
            await admin_create_session(
                entry_fee=0.1,  # Default to 0.1 WLDD
                duration_hours=24,
                api_key=os.getenv("ADMIN_API_KEY"),
                db=db
            )
            print("New session created")
            
    except Exception as e:
        print(f"Error in session checker: {str(e)}")
    finally:
        db.close()

# Start scheduler when app starts
@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(
        check_and_end_sessions,
        IntervalTrigger(minutes=1),  # Check every minute
        id='session_checker'
    )
    scheduler.start()

@app.on_event("startup")
async def startup_event():
    logger.info("Starting Bungo API server")
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'development')}")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Bungo API server")

@app.get("/sessions/active/attempts", response_model=List[AttemptResponse])
async def get_active_session_attempts(
    credentials: Optional[WorldIDCredentials] = Depends(verify_world_id_credentials),
    limit: int = 10, 
    offset: int = 0,
    db: Session = Depends(get_db)
):
    print("Starting get_active_session_attempts")
    
    if not credentials:
        print("No credentials found")
        raise HTTPException(status_code=401, detail="World ID verification required")
    
    wldd_id = credentials.nullifier_hash
    print(f"Got wldd_id: {wldd_id}")
    
    # First get active session
    active_session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    
    print(f"Active session query result: {active_session}")
    
    if not active_session:
        print("No active session found in database")
        raise HTTPException(status_code=404, detail="No active session found")
    
    # Get attempts for this session AND this user
    attempts = db.query(DBAttempt)\
        .filter(
            DBAttempt.session_id == active_session.id,
            DBAttempt.wldd_id == wldd_id  # Add user filter
        )\
        .order_by(DBAttempt.score.desc())\
        .offset(offset)\
        .limit(limit)\
        .all()
    
    return [AttemptResponse(
        id=attempt.id,
        session_id=attempt.session_id,
        wldd_id=attempt.wldd_id,
        messages=[
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in attempt.messages
        ],
        score=attempt.score,
        messages_remaining=attempt.messages_remaining,
        total_pot=active_session.total_pot,
        earnings=attempt.earnings,
        is_free_attempt=attempt.is_free_attempt
    ) for attempt in attempts]

@app.get("/session/{session_id}/leaderboard/{attempt_type}")
async def get_session_leaderboard(session_id: str, attempt_type: str, db: Session = Depends(get_db)):
    """Get top 10 attempts for a specific session and attempt type (free or paid)"""
    if attempt_type not in ["free", "paid"]:
        raise HTTPException(status_code=400, detail="attempt_type must be either 'free' or 'paid'")
        
    top_attempts = db.query(
        DBAttempt.score,
        DBUser.name
    ).join(
        DBUser, DBAttempt.wldd_id == DBUser.wldd_id
    ).filter(
        DBAttempt.session_id == session_id,
        DBAttempt.score.isnot(None),  # Only include attempts with scores
        DBAttempt.is_free_attempt == (attempt_type == "free")  # Filter by attempt type
    ).order_by(
        DBAttempt.score.desc()
    ).limit(8).all()
    
    return [{"name": name, "score": float(score)} for score, name in top_attempts]