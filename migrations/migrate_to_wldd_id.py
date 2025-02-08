from sqlalchemy import create_engine, text
from src.database import DATABASE_URL

def migrate():
    engine = create_engine(DATABASE_URL)
    with engine.connect() as conn:
        with conn.begin():
            # 1. Add wldd_id column to attempts
            conn.execute(text("""
                ALTER TABLE attempts 
                ADD COLUMN wldd_id VARCHAR;
            """))
            
            # 2. Add wldd_id column to payments
            conn.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN wldd_id VARCHAR;
            """))
            
            # 3. Copy data from user_id to wldd_id via users table for attempts
            conn.execute(text("""
                UPDATE attempts a 
                SET wldd_id = u.wldd_id 
                FROM users u 
                WHERE a.user_id = u.id;
            """))
            
            # 4. Copy data from user_id to wldd_id via users table for payments
            conn.execute(text("""
                UPDATE payments p
                SET wldd_id = u.wldd_id 
                FROM users u 
                WHERE p.user_id = u.id;
            """))
            
            # 5. Make wldd_id not nullable in both tables
            conn.execute(text("""
                ALTER TABLE attempts 
                ALTER COLUMN wldd_id SET NOT NULL;
            """))
            conn.execute(text("""
                ALTER TABLE payments 
                ALTER COLUMN wldd_id SET NOT NULL;
            """))
            
            # 6. Add foreign key constraints
            conn.execute(text("""
                ALTER TABLE attempts
                ADD CONSTRAINT fk_attempts_wldd_id
                FOREIGN KEY (wldd_id)
                REFERENCES users(wldd_id);
            """))
            conn.execute(text("""
                ALTER TABLE payments
                ADD CONSTRAINT fk_payments_wldd_id
                FOREIGN KEY (wldd_id)
                REFERENCES users(wldd_id);
            """))
            
            # 7. Drop old user_id columns
            conn.execute(text("""
                ALTER TABLE attempts
                DROP COLUMN user_id;
            """))
            conn.execute(text("""
                ALTER TABLE payments
                DROP COLUMN user_id;
            """))

if __name__ == "__main__":
    migrate() 