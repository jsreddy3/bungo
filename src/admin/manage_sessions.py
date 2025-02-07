# src/admin/manage_sessions.py

import sys
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from uuid import UUID
from tabulate import tabulate  # We'll use this for nice table formatting

# Add the parent directory to the Python path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from src.database import SessionLocal
from src.models.game import SessionStatus
from src.models.database_models import DBSession, DBAttempt, DBMessage, DBUser

UTC = ZoneInfo("UTC")

def create_session(entry_fee: float = 10.0, duration_hours: int = 1) -> DBSession:
    """Create a new active session"""
    db = SessionLocal()
    try:
        # Check for existing active session
        active_session = db.query(DBSession).filter(
            DBSession.status == SessionStatus.ACTIVE.value
        ).first()
        
        if active_session:
            print("Error: An active session already exists!")
            print(f"Session ID: {active_session.id}")
            print(f"Started: {active_session.start_time}")
            print(f"Ends: {active_session.end_time}")
            return active_session
        
        # Create new session
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
        
        print("Successfully created new session!")
        print(f"Session ID: {new_session.id}")
        print(f"Entry Fee: {entry_fee} WLDD")
        print(f"Start Time: {start_time}")
        print(f"End Time: {end_time}")
        
        return new_session
        
    finally:
        db.close()

def end_session(session_id: str = None) -> None:
    """End the active session or a specific session by ID"""
    db = SessionLocal()
    try:
        query = db.query(DBSession)
        if session_id:
            session = query.filter(DBSession.id == session_id).first()
        else:
            session = query.filter(DBSession.status == SessionStatus.ACTIVE.value).first()
        
        if not session:
            print("No active session found!")
            return
        
        session.status = SessionStatus.COMPLETED.value
        db.commit()
        
        print(f"Successfully ended session {session.id}")
        print(f"Final pot: {session.total_pot} WLDD")
        
        # Show winners if any
        winners = [attempt for attempt in session.attempts if attempt.score > 7.0]
        if winners:
            print("\nWinning attempts:")
            for attempt in winners:
                print(f"User {attempt.user_id}: Score {attempt.score}")
        else:
            print("\nNo winning attempts in this session")
            
    finally:
        db.close()

def show_session_details(session_id: str) -> None:
    """Show detailed information about a specific session"""
    db = SessionLocal()
    try:
        session = db.query(DBSession).filter(DBSession.id == UUID(session_id)).first()
        if not session:
            print(f"No session found with ID: {session_id}")
            return

        print("\nSession Details:")
        print("-" * 50)
        print(f"ID: {session.id}")
        print(f"Status: {session.status}")
        print(f"Start Time: {session.start_time}")
        print(f"End Time: {session.end_time}")
        print(f"Entry Fee: {session.entry_fee} WLDD")
        print(f"Total Pot: {session.total_pot} WLDD")
        
        if not session.attempts:
            print("\nNo attempts in this session")
            return

        # Prepare attempt data for tabulation
        attempt_data = []
        for attempt in session.attempts:
            user = db.query(DBUser).filter(DBUser.id == attempt.user_id).first()
            attempt_data.append([
                str(attempt.id),  # Full UUID
                user.wldd_id if user else "Unknown",
                attempt.score or "Not scored",
                len(attempt.messages),
                attempt.messages_remaining,
                "✓" if attempt.score and attempt.score > 7.0 else "✗"
            ])

        print("\nAttempts:")
        print(tabulate(
            attempt_data,
            headers=["ID", "User", "Score", "Messages", "Remaining", "Winner"],
            tablefmt="grid",
            maxcolwidths=[None, 20, 10, 10, 10, 8]  # Allow ID column to be full width
        ))

    finally:
        db.close()

def show_attempt_details(attempt_id: str) -> None:
    """Show detailed information about a specific attempt"""
    db = SessionLocal()
    try:
        attempt = db.query(DBAttempt).filter(DBAttempt.id == UUID(attempt_id)).first()
        if not attempt:
            print(f"No attempt found with ID: {attempt_id}")
            return

        user = db.query(DBUser).filter(DBUser.id == attempt.user_id).first()
        
        print("\nAttempt Details:")
        print("-" * 50)
        print(f"ID: {attempt.id}")
        print(f"User ID: {user.wldd_id if user else 'Unknown'}")
        print(f"Score: {attempt.score or 'Not scored'}")
        print(f"Messages Used: {len(attempt.messages)}")
        print(f"Messages Remaining: {attempt.messages_remaining}")
        print(f"Winner: {'Yes' if attempt.score and attempt.score > 7.0 else 'No'}")

        if attempt.messages:
            print("\nConversation:")
            print("-" * 50)
            for i, msg in enumerate(attempt.messages, 1):
                print(f"\nMessage {i}:")
                print(f"User: {msg.content}")
                print(f"AI: {msg.ai_response}")

    finally:
        db.close()

def show_user_stats(user_id: str = None, wldd_id: str = None) -> None:
    """Show detailed statistics for a user"""
    db = SessionLocal()
    try:
        if wldd_id:
            user = db.query(DBUser).filter(DBUser.wldd_id == wldd_id).first()
        else:
            user = db.query(DBUser).filter(DBUser.id == UUID(user_id)).first()

        if not user:
            print("User not found!")
            return

        print("\nUser Details:")
        print("-" * 50)
        print(f"ID: {user.id}")
        print(f"WLDD ID: {user.wldd_id}")
        print(f"Created: {user.created_at}")
        print(f"Last Active: {user.last_active}")

        # Get user statistics
        total_attempts = len(user.attempts)
        winning_attempts = len([a for a in user.attempts if a.score and a.score > 7.0])
        total_messages = sum(len(a.messages) for a in user.attempts)
        total_earnings = sum(a.earnings for a in user.attempts if a.earnings)
        
        print("\nStatistics:")
        print(f"Total Attempts: {total_attempts}")
        print(f"Winning Attempts: {winning_attempts}")
        print(f"Win Rate: {(winning_attempts/total_attempts*100 if total_attempts else 0):.1f}%")
        print(f"Total Messages Sent: {total_messages}")
        print(f"Total Earnings: {total_earnings} WLDD")

        # Show recent attempts
        if user.attempts:
            print("\nRecent Attempts:")
            attempt_data = []
            for attempt in sorted(user.attempts, key=lambda x: x.created_at, reverse=True)[:5]:
                session = db.query(DBSession).filter(DBSession.id == attempt.session_id).first()
                attempt_data.append([
                    str(attempt.id),
                    session.start_time.strftime("%Y-%m-%d %H:%M") if session else "Unknown",
                    attempt.score or "Not scored",
                    len(attempt.messages),
                    attempt.earnings or 0
                ])
            
            print(tabulate(
                attempt_data,
                headers=["ID", "Date", "Score", "Messages", "Earnings"],
                tablefmt="grid",
                maxcolwidths=[None, 25, 10, 10, 10]
            ))

    finally:
        db.close()

def list_sessions(show_attempts: bool = False) -> None:
    """List all sessions and their status"""
    db = SessionLocal()
    try:
        sessions = db.query(DBSession).order_by(DBSession.start_time.desc()).all()
        
        session_data = []
        for session in sessions:
            winners = len([a for a in session.attempts if a.score and a.score > 7.0])
            session_data.append([
                str(session.id),  # Full UUID
                session.status,
                session.start_time.strftime("%Y-%m-%d %H:%M"),  # Format datetime as string
                session.end_time.strftime("%Y-%m-%d %H:%M"),    # Format datetime as string
                session.entry_fee,
                session.total_pot,
                len(session.attempts),
                winners
            ])

        print("\nAll Sessions:")
        print(tabulate(
            session_data,
            headers=["ID", "Status", "Start", "End", "Fee", "Pot", "Attempts", "Winners"],
            tablefmt="grid",
            maxcolwidths=[None, 15, 25, 25, 8, 10, 10, 10]
        ))
            
    finally:
        db.close()

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Manage game sessions")
    parser.add_argument("action", choices=[
        "create", "end", "list", 
        "show-session", "show-attempt", "show-user"
    ], help="Action to perform")
    
    # Session management arguments
    parser.add_argument("--fee", type=float, default=10.0, help="Entry fee for new session")
    parser.add_argument("--duration", type=int, default=1, help="Session duration in hours")
    parser.add_argument("--session-id", help="Session ID for specific operations")
    
    # Attempt and user arguments
    parser.add_argument("--attempt-id", help="Attempt ID for detailed view")
    parser.add_argument("--user-id", help="User ID for statistics")
    parser.add_argument("--wldd-id", help="WLDD ID for user lookup")
    
    args = parser.parse_args()
    
    if args.action == "create":
        create_session(args.fee, args.duration)
    elif args.action == "end":
        end_session(args.session_id)
    elif args.action == "list":
        list_sessions()
    elif args.action == "show-session":
        if not args.session_id:
            print("Error: --session-id is required for show-session")
        else:
            show_session_details(args.session_id)
    elif args.action == "show-attempt":
        if not args.attempt_id:
            print("Error: --attempt-id is required for show-attempt")
        else:
            show_attempt_details(args.attempt_id)
    elif args.action == "show-user":
        if not (args.user_id or args.wldd_id):
            print("Error: either --user-id or --wldd-id is required for show-user")
        else:
            show_user_stats(args.user_id, args.wldd_id) 