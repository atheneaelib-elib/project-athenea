from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
try:
    from .settings import settings
except (ImportError, ValueError):
    from settings import settings

DATABASE_URL = (
    f"postgresql+psycopg2://"
    f"{settings.DB_USER}:"
    f"{settings.DB_PASSWORD}@"
    f"{settings.DB_HOST}:"
    f"{settings.DB_PORT}/"
    f"{settings.DB_NAME}"
)

engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


def reinit_engine(db_user=None, db_password=None, db_host=None, db_port=None, db_name=None):
    """Reinitialize SQLAlchemy engine and session pool with updated credentials."""
    global DATABASE_URL, engine, SessionLocal
    user = db_user if db_user is not None else settings.DB_USER
    password = db_password if db_password is not None else settings.DB_PASSWORD
    host = db_host if db_host is not None else settings.DB_HOST
    port = db_port if db_port is not None else settings.DB_PORT
    name = db_name if db_name is not None else settings.DB_NAME

    DATABASE_URL = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
    engine = create_engine(
        DATABASE_URL,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True
    )
    SessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine
    )
    return engine


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
