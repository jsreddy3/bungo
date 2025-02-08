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
            
            # 2. Copy data from user_id to wldd_id via users table
            conn.execute(text("""
                UPDATE attempts a 
                SET wldd_id = u.wldd_id 
                FROM users u 
                WHERE a.user_id = u.id;
            """))
            
            # 3. Make wldd_id not nullable
            conn.execute(text("""
                ALTER TABLE attempts 
                ALTER COLUMN wldd_id SET NOT NULL;
            """))
            
            # 4. Add foreign key constraint
            conn.execute(text("""
                ALTER TABLE attempts
                ADD CONSTRAINT fk_attempts_wldd_id
                FOREIGN KEY (wldd_id)
                REFERENCES users(wldd_id);
            """))
            
            # 5. Drop old user_id column
            conn.execute(text("""
                ALTER TABLE attempts
                DROP COLUMN user_id;
            """))

if __name__ == "__main__":
    migrate() 