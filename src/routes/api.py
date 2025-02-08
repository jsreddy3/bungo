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

class VerifyRequest(BaseModel):
    nullifier_hash: str
    merkle_root: str
    proof: str
    verification_level: str
    action: str
    signal_hash: str = None

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
            winning_attempts=[]
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
        winning_attempts=[attempt.id for attempt in session.attempts if attempt.score > 7.0]
    )
    print(f"Total request took: {time.time() - start_time:.2f}s")
    return response

@app.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: UUID, db: Session = Depends(get_db)):
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
async def create_attempt(
    user_id: UUID,
    wldd_id: str,
    payment_reference: str = None,  # Make optional
    credentials: dict = Depends(verify_world_id_credentials),
    db: Session = Depends(get_db)
):
    """Create a new attempt"""
    is_dev_mode = os.getenv("ENVIRONMENT") == "development"
    
    # Only check credentials and payment in production
    if not is_dev_mode:
        if not credentials:
            raise HTTPException(status_code=401, detail="World ID verification required")
        
        verify_response = await verify_stored_credentials(credentials, db)
        if not verify_response["success"]:
            raise HTTPException(status_code=401, detail="Invalid World ID credentials")
            
        # Verify payment
        if not payment_reference:
            raise HTTPException(status_code=400, detail="Payment reference required")
            
        payment = db.query(DBPayment).filter(
            DBPayment.reference == payment_reference,
            DBPayment.user_id == user_id,
            DBPayment.status == "confirmed"
        ).first()
        
        if not payment:
            raise HTTPException(status_code=400, detail="Payment required")
    
    # Verify user owns this WLDD ID
    user = db.query(DBUser).filter(DBUser.id == user_id).first()
    if not user or user.wldd_id != wldd_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
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
async def get_attempt(
    attempt_id: UUID,
    wldd_id: str,  # Add WLDD ID to request
    db: Session = Depends(get_db)
):
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    # Verify ownership
    user = db.query(DBUser).filter(DBUser.id == attempt.user_id).first()
    if not user or user.wldd_id != wldd_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
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

@app.post("/attempts/{attempt_id}/score", response_model=AttemptResponse)
async def score_attempt(
    attempt_id: UUID,
    wldd_id: str,  # Add WLDD ID to request
    db: Session = Depends(get_db),
    llm_service: LLMService = Depends(get_llm_service)
):
    attempt = db.query(DBAttempt).filter(DBAttempt.id == attempt_id).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")
    
    # Verify ownership
    user = db.query(DBUser).filter(DBUser.id == attempt.user_id).first()
    if not user or user.wldd_id != wldd_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Check if already scored
    if attempt.score is not None:
        raise HTTPException(status_code=400, detail="Attempt already scored")
    
    # Check if all messages used
    if attempt.messages_remaining > 0:
        raise HTTPException(status_code=400, detail="Must use all messages first")
    
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
        user_id=attempt.user_id,
        messages=[
            MessageResponse(
                content=msg.content,
                ai_response=msg.ai_response
            ) for msg in attempt.messages
        ],
        is_winner=attempt.score > 7.0,
        messages_remaining=attempt.messages_remaining,
        total_pot=attempt.total_pot
    )

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

@app.post("/verify")
async def verify_world_id(
    request: VerifyRequest,
    db: Session = Depends(get_db)
):
    """Verify World ID proof and create user if needed"""
    app_id = os.getenv("WORLD_ID_APP_ID")
    print(f"Verifying with app_id: {app_id}")
    print(f"Request data: {request.dict()}")
    
    try:
        # Check if user has already verified today
        today = date.today()
        existing_verification = db.query(DBVerification).filter(
            and_(
                DBVerification.nullifier_hash == request.nullifier_hash,
                DBVerification.created_at >= today
            )
        ).first()
        
        if existing_verification:
            raise HTTPException(
                status_code=400, 
                detail="Already verified today"
            )
        
        # Verify with World ID API
        async with httpx.AsyncClient() as client:
            verify_url = f"https://developer.worldcoin.org/api/v2/verify/{app_id}"
            verify_data = {
                "nullifier_hash": request.nullifier_hash,
                "merkle_root": request.merkle_root,
                "proof": request.proof,
                "verification_level": request.verification_level,
                "action": request.action,
                "signal_hash": request.signal_hash
            }
            print(f"Making request to: {verify_url}")
            print(f"With data: {verify_data}")
            
            response = await client.post(verify_url, json=verify_data)
            print(f"World ID API response status: {response.status_code}")
            print(f"World ID API response: {response.text}")
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400,
                    detail="World ID verification failed"
                )
            
            verify_response = response.json()
            
            # Extract WLDD ID from action
            # Assuming action format is "play_bungo_WLDD-12345678"
            wldd_id = request.action.split("_")[-1]
            if not wldd_id.startswith("WLDD-"):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid WLDD ID in action"
                )
            
            # Create user if they don't exist
            user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
            if not user:
                user = DBUser(
                    wldd_id=wldd_id,
                    created_at=datetime.now(UTC),
                    last_active=datetime.now(UTC)
                )
                db.add(user)
            else:
                user.last_active = datetime.now(UTC)
            
            # Store verification
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
                    "id": user.id,
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
    user_id: UUID,
    db: Session = Depends(get_db)
):
    """Initialize a payment for game attempt"""
    # Generate unique reference
    reference = secrets.token_hex(16)
    
    # Store payment reference
    payment = DBPayment(
        reference=reference,
        user_id=user_id,
        created_at=datetime.now(UTC)
    )
    db.add(payment)
    db.commit()
    
    return PaymentInitResponse(
        reference=reference,
        recipient=os.getenv("PAYMENT_RECIPIENT_ADDRESS"),
        amount=10.0  # Entry fee in WLD
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
                params={"app_id": os.getenv("WORLD_ID_APP_ID")},
                headers={
                    "Authorization": f"Bearer {os.getenv('DEV_PORTAL_API_KEY')}"
                }
            )
            
            transaction = response.json()
            
            if (transaction["reference"] == request.reference and 
                transaction["status"] != "failed"):
                payment.status = "confirmed"
                payment.transaction_id = request.payload["transaction_id"]
                db.commit()
                return {"success": True}
            
            payment.status = "failed"
            db.commit()
            return {"success": False}
            
    except Exception as e:
        print(f"Payment confirmation failed: {e}")
        return {"success": False, "error": str(e)}

async def verify_world_id_credentials(request: Request):
    """Middleware to verify World ID credentials from headers"""
    # Check if we're in dev mode
    is_dev_mode = os.getenv("ENVIRONMENT") == "development"
    
    credentials = request.headers.get('X-WorldID-Credentials')
    if not credentials:
        if is_dev_mode:
            return None  # Allow in dev mode
        return None
        
    try:
        creds = json.loads(credentials)
        return WorldIDCredentials(
            nullifier_hash=creds["nullifier_hash"],
            merkle_root=creds["merkle_root"],
            proof=creds["proof"],
            verification_level=creds["verification_level"]
        )
    except:
        if is_dev_mode:
            return None  # Allow in dev mode
        return None

async def verify_stored_credentials(credentials: dict, db: Session):
    """Verify stored World ID credentials"""
    # Check if verification exists in our DB
    verification = db.query(DBVerification).filter(
        DBVerification.nullifier_hash == credentials["nullifier_hash"],
        DBVerification.merkle_root == credentials["merkle_root"]
    ).first()
    
    if not verification:
        return {"success": False, "error": "Verification not found"}
        
    # Check if it's still valid (same day)
    if verification.created_at.date() != datetime.now(UTC).date():
        return {"success": False, "error": "Verification expired"}
        
    return {"success": True}