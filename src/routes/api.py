# src/routes/api.py

from dotenv import load_dotenv
load_dotenv()  # Add this line before any other imports

from fastapi import FastAPI, HTTPException, Depends, Request
from typing import List, Optional
from pydantic import BaseModel, Field
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
from src.routes.admin import router as admin_router, get_api_key
from src.routes.admin_ui import router as admin_ui_router
from fastapi.templating import Jinja2Templates
from fastapi import BackgroundTasks
import time
import httpx
import os
from sqlalchemy import and_
import secrets
import json

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
   wldd_id: str
   messages: List[MessageResponse]
   score: Optional[float]
   messages_remaining: int
   total_pot: float
   earnings: Optional[float] = None
   
class SessionResponse(BaseModel):
   id: UUID
   start_time: datetime
   end_time: datetime
   entry_fee: float
   total_pot: float
   status: str
   attempts: List[dict]
   winning_conversation: Optional[List[MessageResponse]] = None

# At the top of api.py with other models
class UserResponse(BaseModel):
    wldd_id: str
    stats: dict

    class Config:
        from_attributes = True

class VerifyRequest(BaseModel):
    nullifier_hash: str
    merkle_root: str
    proof: str
    verification_level: str
    action: str
    signal: str  # Add this field

class PaymentInitResponse(BaseModel):
    reference: str
    recipient: str  # The address that receives the payment
    amount: float

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

# Move these helper functions before the routes
async def verify_world_id_credentials(
    request: Request,
    db: Session = Depends(get_db)
) -> Optional[WorldIDCredentials]:
    """Verify World ID credentials and check against stored verifications"""
    is_dev_mode = os.getenv("ENVIRONMENT") == "development"
    
    credentials = request.headers.get('X-WorldID-Credentials')
    if not credentials:
        if is_dev_mode:
            return None
        return None
        
    try:
        print(f"Credentials: {credentials}")
        creds = json.loads(credentials)
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
            return None
            
        return parsed_creds
        
    except:
        if is_dev_mode:
            return None
        return None

# Modify session creation to be more explicit about timing
@app.post("/sessions/create", response_model=SessionResponse)
async def create_session(
    entry_fee: float,
    duration_hours: int = 1,
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
    print("Starting get_current_session")
    start_time = time.time()
    
    session = db.query(DBSession).filter(
        DBSession.status == SessionStatus.ACTIVE.value
    ).first()
    print(f"DB query took: {time.time() - start_time:.2f}s")
    
    if not session:
        raise HTTPException(status_code=404, detail="No active session found")
    
    now = datetime.now(UTC)
    print(f"Processing session {session.id}")
    
    if now > session.end_time:
        print(f"Marking session {session.id} as completed")
        session.status = SessionStatus.COMPLETED.value
        db.commit()
        print(f"DB commit took: {time.time() - start_time:.2f}s")
    
    response = SessionResponse(
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
    print(f"Total request took: {time.time() - start_time:.2f}s")
    return response

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
        # Calculate total score across all attempts
        total_score = sum(attempt.score for attempt in attempts)
        pot = session.total_pot
        
        # Distribute pot based on relative scores
        for attempt in attempts:
            # Calculate proportional share of pot
            share = (attempt.score / total_score) * pot if total_score > 0 else 0
            attempt.earnings = share
        
        # Find highest scoring attempts
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
            'earnings': attempt.earnings
        } for attempt in attempts],
        winning_conversation=winning_conversation
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
            
    # Verify payment
    if credentials or not is_dev_mode:
        if not request.payment_reference:
            raise HTTPException(status_code=400, detail="Payment reference required")
            
        # Get active session first to check fee
        active_session = db.query(DBSession).filter(
            DBSession.status == SessionStatus.ACTIVE.value
        ).first()
        
        if not active_session:
            raise HTTPException(status_code=400, detail="No active session")
            
        print(f"Looking for payment with reference: {request.payment_reference}")
        print(f"User wldd_id: {wldd_id}")
        payment = db.query(DBPayment).with_for_update().filter(
            DBPayment.reference == request.payment_reference,
            DBPayment.wldd_id == wldd_id,
            DBPayment.status == "confirmed",
            DBPayment.consumed == False,
            DBPayment.amount == active_session.entry_fee
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
    
    # Get user by wldd_id
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    new_attempt = DBAttempt(
        id=uuid4(),
        session_id=active_session.id,
        wldd_id=wldd_id,
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
        earnings=None
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
        earnings=attempt.earnings
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
    score = await llm_service.score_conversation(
        [Message(content=msg.content, timestamp=msg.timestamp)
         for msg in attempt.messages]
    )
    attempt.score = score
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
        earnings=attempt.earnings
    )

@app.post("/attempts/{attempt_id}/message", response_model=MessageResponse)
async def submit_message(
    attempt_id: UUID,
    message: MessageRequest,
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service),
):
    # First get the attempt and check messages remaining
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
        
    if attempt.messages_remaining <= 0:
        raise HTTPException(
            status_code=400, 
            detail="No messages remaining for this attempt. Purchase more messages to continue."
        )
    
    conversation_manager = ConversationManager(llm_service, db)
    try:
        message_result = await conversation_manager.process_attempt_message(
            attempt_id,
            message.content
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
        id=uuid4(),
        wldd_id=request.wldd_id,
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC)
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return UserResponse(
        wldd_id=new_user.wldd_id,
        stats=new_user.get_stats()
    )

@app.get("/users/{wldd_id}", response_model=UserResponse)
async def get_user(wldd_id: str, db: Session = Depends(get_db)):
    """Get user details by WLDD ID"""
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
        wldd_id=user.wldd_id,
        stats=user.get_stats()
    )

@app.get("/users/{wldd_id}/stats")
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

@app.get("/users/{wldd_id}/attempts", response_model=List[AttemptResponse])
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
        earnings=attempt.earnings
    ) for attempt in attempts]

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

@app.post("/verify")
async def verify_world_id(request: VerifyRequest, db: Session = Depends(get_db)):
    try:
        # First check if user exists (using nullifier_hash as ID)
        user = db.query(DBUser).filter(
            DBUser.wldd_id == request.nullifier_hash
        ).first()
        
        # Check if user has already verified today BEFORE trying World ID
        today = date.today()
        existing_verification = db.query(DBVerification).filter(
            and_(
                DBVerification.nullifier_hash == request.nullifier_hash,
                DBVerification.created_at >= today
            )
        ).first()
        
        if existing_verification:
            # If user doesn't exist but has verification, create user
            if not user:
                user = DBUser(
                    wldd_id=request.nullifier_hash,
                    created_at=datetime.now(UTC),
                    last_active=datetime.now(UTC)
                )
                db.add(user)
                db.commit()
            else:
                user.last_active = datetime.now(UTC)
                db.commit()

            # Return success with existing verification
            return {
                "success": True,
                "verification": {
                    "nullifier_hash": existing_verification.nullifier_hash,
                    "merkle_root": existing_verification.merkle_root,
                    "action": existing_verification.action
                },
                "user": {
                    "wldd_id": user.wldd_id
                }
            }

        # Only call World ID API if we don't have a valid verification
        verify_data = {
            "nullifier_hash": request.nullifier_hash,
            "merkle_root": request.merkle_root,
            "proof": request.proof,
            "verification_level": request.verification_level,
            "action": request.action,
            "signal": request.signal
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
            
            return {
                "success": True,
                "verification": verify_response,
                "user": {
                    "wldd_id": user.wldd_id
                }
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
    print(f"Initiate payment credentials: {credentials}")  # Debug log
    if not credentials:
        print("No credentials in initiate_payment")  # Debug log
        raise HTTPException(status_code=401, detail="World ID verification required")
        
    # Get user from credentials
    wldd_id = credentials.nullifier_hash
    user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
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
async def confirm_payment(
    request: PaymentConfirmRequest,
    db: Session = Depends(get_db)
):
    """Confirm a payment using World ID API"""
    payment = db.query(DBPayment).filter(
        DBPayment.reference == request.reference
    ).first()
    
    if not payment:
        return {"success": False, "error": "Payment not found"}
        
    try:
        # Verify transaction with World ID API
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
                transaction.get("transaction_status") != "failed"):  # Changed from status to transaction_status
                payment.status = "confirmed"
                payment.transaction_id = request.payload["transaction_id"]
                payment.amount = float(int(transaction.get("token_amount", "0")) * 10**-6)  # Convert from BigInt with 6 decimals
                db.commit()
                return {"success": True}
            
            payment.status = "failed"
            db.commit()
            return {"success": False}
            
    except Exception as e:
        print(f"Payment confirmation failed: {e}")
        return {"success": False, "error": str(e)}