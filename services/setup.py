import os
import logging
from pathlib import Path
import psycopg
from sqlalchemy import text

try:
    from ..settings import settings
except ImportError:
    from settings import settings

try:
    from . import auth as auth_service
except ImportError:
    import auth as auth_service

try:
    from .. import db as db_module
except ImportError:
    import db as db_module

logger = logging.getLogger("athenea.setup")

_CONFIGURED_CACHE = None
SCHEMA_SQL_PATH = Path(__file__).resolve().parent.parent / "schema.sql"
LOCAL_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
SYSTEM_ENV_PATH = Path("/etc/athenea.env")


def get_target_env_write_path() -> Path:
    """Return the preferred path to write configuration."""
    if SYSTEM_ENV_PATH.exists() and os.access(SYSTEM_ENV_PATH, os.W_OK):
        return SYSTEM_ENV_PATH
    return LOCAL_ENV_PATH


def is_system_configured() -> bool:
    """
    Check if Athenea has already completed initial setup.
    Cached in memory to introduce zero overhead on subsequent requests.
    """
    global _CONFIGURED_CACHE
    if _CONFIGURED_CACHE is True:
        return True

    # Check if database connects and at least one active admin exists
    try:
        engine = db_module.engine
        with engine.connect() as conn:
            # Check if users table exists and has an admin
            res = conn.execute(text("SELECT 1 FROM users WHERE role = 'admin' AND is_active = true LIMIT 1")).fetchone()
            if res:
                _CONFIGURED_CACHE = True
                return True
    except Exception as e:
        logger.warning(f"is_system_configured check failed: {e}")

    return False


def set_configured_flag(val: bool = True):
    global _CONFIGURED_CACHE
    _CONFIGURED_CACHE = val


def test_database_connection(host: str, port: int, dbname: str, user: str, password: str) -> tuple[bool, str]:
    """Test connecting to PostgreSQL server and target database."""
    # 1. First test target database directly
    try:
        conn = psycopg.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
            connect_timeout=5
        )
        conn.close()
        return True, f"Successfully connected to database '{dbname}'!"
    except Exception as e:
        err_msg = str(e)
        # If database doesn't exist, check if server connection works via 'postgres' DB
        if "does not exist" in err_msg.lower():
            try:
                pg_conn = psycopg.connect(
                    host=host,
                    port=port,
                    dbname="postgres",
                    user=user,
                    password=password,
                    connect_timeout=5
                )
                pg_conn.close()
                return True, f"Connected to PostgreSQL server! Target database '{dbname}' will be created during setup."
            except Exception as se:
                return False, f"Server authentication failed: {se}"
        return False, f"Connection failed: {err_msg}"


def complete_initial_setup(
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_password: str,
    library_root: str,
    admin_username: str,
    admin_password: str,
    admin_email: str = "",
    allow_registration: bool = True,
    require_approval: bool = True,
    smtp_host: str = "",
    smtp_port: int = 587,
    smtp_user: str = "",
    smtp_password: str = "",
    smtp_from: str = "books@example.com"
) -> tuple[bool, str]:
    """
    Execute full initial provisioning for Athenea:
    1. Write .env file
    2. Create PostgreSQL database if needed
    3. Run schema.sql DDL
    4. Create primary Administrator account
    5. Reinitialize live SQLAlchemy engine
    """
    # If db_password was left blank in the web form, fallback to existing configuration
    if not db_password or not db_password.strip():
        if SYSTEM_ENV_PATH.exists():
            try:
                for line in SYSTEM_ENV_PATH.read_text(encoding="utf-8").splitlines():
                    if line.startswith("DB_PASSWORD="):
                        db_password = line.split("=", 1)[1].strip()
                        break
            except Exception:
                pass
        if not db_password and settings.DB_PASSWORD:
            db_password = settings.DB_PASSWORD

    # 1. Ensure target database exists
    try:
        # Try direct connection
        target_conn = psycopg.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password,
            autocommit=True
        )
        target_conn.close()
    except Exception:
        # Connect to 'postgres' database and create the target db
        try:
            admin_conn = psycopg.connect(
                host=db_host,
                port=db_port,
                dbname="postgres",
                user=db_user,
                password=db_password,
                autocommit=True
            )
            cur = admin_conn.cursor()
            cur.execute(f'CREATE DATABASE "{db_name}";')
            cur.close()
            admin_conn.close()
        except Exception as ce:
            logger.warning(f"Could not auto-create database '{db_name}': {ce}")

    # 2. Write configuration file
    env_file = get_target_env_write_path()
    env_lines = [
        "# ===================================================================\n",
        "# Athenea - Environment Configuration\n",
        "# Generated by Setup Wizard\n",
        "# ===================================================================\n\n",
        f"DB_HOST={db_host}\n",
        f"DB_PORT={db_port}\n",
        f"DB_NAME={db_name}\n",
        f"DB_USER={db_user}\n",
        f"DB_PASSWORD={db_password}\n",
        f"LIBRARY_ROOT={library_root}\n",
        f"ALLOW_REGISTRATION={'true' if allow_registration else 'false'}\n",
        f"REQUIRE_ADMIN_APPROVAL={'true' if require_approval else 'false'}\n",
        f"SMTP_HOST={smtp_host}\n",
        f"SMTP_PORT={smtp_port}\n",
        f"SMTP_USER={smtp_user}\n",
        f"SMTP_PASSWORD={smtp_password}\n",
        f"SMTP_FROM={smtp_from}\n",
        "SMTP_TLS=true\n",
        "SMTP_SSL=false\n"
    ]
    try:
        env_file.write_text("".join(env_lines), encoding="utf-8")
    except Exception as we:
        return False, f"Failed to save configuration file: {we}"

    # Update runtime environment and settings
    os.environ["DB_HOST"] = str(db_host)
    os.environ["DB_PORT"] = str(db_port)
    os.environ["DB_NAME"] = str(db_name)
    os.environ["DB_USER"] = str(db_user)
    os.environ["DB_PASSWORD"] = str(db_password)
    os.environ["LIBRARY_ROOT"] = str(library_root)

    settings.DB_HOST = str(db_host)
    settings.DB_PORT = int(db_port)
    settings.DB_NAME = str(db_name)
    settings.DB_USER = str(db_user)
    settings.DB_PASSWORD = str(db_password)
    settings.LIBRARY_ROOT = str(library_root)
    settings.ALLOW_REGISTRATION = allow_registration
    settings.REQUIRE_ADMIN_APPROVAL = require_approval

    # 3. Apply schema.sql DDL if books table doesn't exist
    conn = psycopg.connect(
        host=db_host,
        port=db_port,
        dbname=db_name,
        user=db_user,
        password=db_password,
        autocommit=True
    )
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'books';")
    has_books_table = bool(cur.fetchone())

    if not has_books_table:
        if not SCHEMA_SQL_PATH.exists():
            return False, f"schema.sql not found at {SCHEMA_SQL_PATH}"
        schema_text = SCHEMA_SQL_PATH.read_text(encoding="utf-8", errors="ignore")
        clean_sql = "\n".join(l for l in schema_text.splitlines() if not l.strip().startswith("\\"))
        try:
            cur.execute(clean_sql)
            cur.execute("SET search_path = public;")
        except Exception as se:
            logger.error(f"Error executing schema.sql: {se}")
            # If failed on extension creation, attempt executing without CREATE EXTENSION
            try:
                fallback_sql = "\n".join(
                    l for l in clean_sql.splitlines() 
                    if not l.strip().upper().startswith("CREATE EXTENSION") and not l.strip().upper().startswith("COMMENT ON EXTENSION")
                )
                cur.execute(fallback_sql)
                cur.execute("SET search_path = public;")
            except Exception as fe:
                cur.close()
                conn.close()
                return False, f"Database schema initialization failed: {fe}"

    cur.close()
    conn.close()

    # 4. Reinitialize live SQLAlchemy connection pool and ensure all auth/permission tables exist
    db_module.reinit_engine(
        db_user=db_user,
        db_password=db_password,
        db_host=db_host,
        db_port=db_port,
        db_name=db_name
    )

    try:
        db = db_module.SessionLocal()
        try:
            auth_service.init_auth_tables(db)
            pwd_hash = auth_service.hash_password(admin_password)
            db.execute(
                text("""
                    INSERT INTO public.users (username, password_hash, role, email, is_active, created_at)
                    VALUES (:u, :p, 'admin', :e, true, NOW())
                    ON CONFLICT (username)
                    DO UPDATE SET password_hash = EXCLUDED.password_hash, role = 'admin', is_active = true
                """),
                {
                    "u": admin_username.strip(),
                    "p": pwd_hash,
                    "e": (admin_email or "").strip() or None,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception as ue:
        return False, f"Failed to create admin user: {ue}"

    set_configured_flag(True)
    return True, "Setup completed successfully! You can now sign in."
