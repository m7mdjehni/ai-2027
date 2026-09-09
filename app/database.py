import os
import time
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from app.models import Base

DATABASE_URL = (
    f"postgresql://{os.getenv('POSTGRES_USER','assistant')}:{os.getenv('POSTGRES_PASSWORD','changeme')}@{os.getenv('POSTGRES_HOST','db')}:{os.getenv('POSTGRES_PORT','5432')}/{os.getenv('POSTGRES_DB','assistantdb')}"
)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db(max_attempts: int = 10, retry_delay: int = 2):
    for attempt in range(1, max_attempts + 1):
        try:
            Base.metadata.create_all(bind=engine, tables=[table for name, table in Base.metadata.tables.items()
                if name not in {'project_runs','project_run_events','project_model_attempts','project_validations','project_publications'}])
            from app.migrations import migrate
            migrate(engine)
            return
        except OperationalError:
            if attempt == max_attempts:
                raise
            time.sleep(retry_delay)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
