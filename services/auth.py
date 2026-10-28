import re
import bcrypt
import secrets
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from ..settings import settings
except ImportError:
    from settings import settings

SESSION_COOKIE_NAME = "book_session"
SESSION_DURATION_DAYS = 30


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


def prune_expired_sessions(db: Session):
    """Clean up expired session tokens from the database."""
    try:
        db.execute(text("DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP"))
        db.commit()
    except Exception:
        db.rollback()


def prune_old_login_attempts(db: Session):
    """Purge login attempts older than 2 hours."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
        db.execute(text("DELETE FROM login_attempts WHERE attempted_at < :cutoff"), {"cutoff": cutoff})
        db.commit()
    except Exception:
        db.rollback()


def check_login_rate_limit(db: Session, ip_address: str, username: str, max_attempts: int = 5, window_seconds: int = 300) -> tuple[bool, int]:
    """Check if IP or username has exceeded allowed failed login attempts across all workers."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    row = db.execute(text("""
        SELECT count(*), max(attempted_at)
        FROM login_attempts
        WHERE (ip_address = :ip OR LOWER(username) = LOWER(:u))
          AND attempted_at > :cutoff
    """), {"ip": ip_address, "u": username.strip(), "cutoff": cutoff}).fetchone()

    count = row[0] or 0
    if count >= max_attempts:
        last_time = row[1]
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds() if last_time else 0
        remaining = max(1, int(window_seconds - elapsed))
        return True, remaining
    return False, 0


def record_failed_login(db: Session, ip_address: str, username: str):
    """Record a failed login attempt in the database."""
    try:
        db.execute(text("""
            INSERT INTO login_attempts (ip_address, username)
            VALUES (:ip, :u)
        """), {"ip": ip_address, "u": username.strip()})
        db.commit()
    except Exception:
        db.rollback()


def clear_failed_logins(db: Session, ip_address: str, username: str):
    """Clear failed login attempts upon successful authentication."""
    try:
        db.execute(text("""
            DELETE FROM login_attempts
            WHERE ip_address = :ip OR LOWER(username) = LOWER(:u)
        """), {"ip": ip_address, "u": username.strip()})
        db.commit()
    except Exception:
        db.rollback()


def init_auth_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(100) UNIQUE NOT NULL,
            email VARCHAR(255),
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'user',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP WITH TIME ZONE
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id VARCHAR(128) PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP WITH TIME ZONE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS favorites (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            book_id BIGINT NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, book_id)
        );

        CREATE TABLE IF NOT EXISTS login_attempts (
            id SERIAL PRIMARY KEY,
            ip_address VARCHAR(64) NOT NULL,
            username VARCHAR(100),
            attempted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
        CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id);
        CREATE INDEX IF NOT EXISTS idx_favorites_book ON favorites(book_id);
        CREATE INDEX IF NOT EXISTS idx_login_attempts_ip ON login_attempts(ip_address, attempted_at);
        CREATE INDEX IF NOT EXISTS idx_login_attempts_user ON login_attempts(LOWER(username), attempted_at);
    """))
    db.commit()

    prune_expired_sessions(db)
    prune_old_login_attempts(db)

    # Check if any user exists. If not, create default admin:admin
    count = db.execute(text("SELECT count(*) FROM users")).scalar() or 0
    if count == 0:
        admin_hash = hash_password("admin")
        db.execute(text("""
            INSERT INTO users (username, email, password_hash, role, is_active)
            VALUES ('admin', 'admin@local.host', :pwd, 'admin', TRUE)
        """), {"pwd": admin_hash})
        db.commit()
        print("Default admin created: username 'admin', password 'admin'")


def create_session(db: Session, user_id: int) -> str:
    session_id = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DURATION_DAYS)

    db.execute(text("""
        INSERT INTO sessions (session_id, user_id, expires_at)
        VALUES (:sid, :uid, :exp)
    """), {"sid": session_id, "uid": user_id, "exp": expires_at})

    # Update last_login
    db.execute(text("""
        UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = :uid
    """), {"uid": user_id})

    db.commit()
    return session_id


def delete_session(db: Session, session_id: str):
    db.execute(text("DELETE FROM sessions WHERE session_id = :sid"), {"sid": session_id})
    db.commit()


def get_user_from_session(db: Session, session_id: str | None):
    if not session_id:
        return None

    row = db.execute(text("""
        SELECT u.id, u.username, u.email, u.kindle_email, u.role, u.is_active, u.created_at, u.last_login
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_id = :sid AND s.expires_at > CURRENT_TIMESTAMP AND u.is_active = TRUE
    """), {"sid": session_id}).fetchone()

    if not row:
        return None

    return {
        "id": row.id,
        "username": row.username,
        "email": row.email,
        "kindle_email": row.kindle_email or "",
        "role": row.role,
        "is_active": row.is_active,
        "is_admin": (row.role == "admin"),
        "is_power_user": (row.role in ("admin", "power_user")),
        "can_download": True,
        "created_at": row.created_at,
        "last_login": row.last_login,
    }


def authenticate_user(db: Session, username: str, password: str):
    clean_u = (username or "").strip()
    clean_p = (password or "").strip()
    if not clean_u or not password:
        return None

    row = db.execute(text("""
        SELECT id, username, password_hash, role, is_active
        FROM users
        WHERE LOWER(username) = LOWER(:u) OR (email IS NOT NULL AND LOWER(email) = LOWER(:u))
    """), {"u": clean_u}).fetchone()

    if not row:
        return None

    # Verify password before checking active status
    valid_pass = verify_password(password, row.password_hash) or verify_password(clean_p, row.password_hash)
    if not valid_pass:
        return None

    # Credentials are verified; now check if account is active
    if not row.is_active:
        return False  # Password correct, but account awaiting approval or disabled

    return {
        "id": row.id,
        "username": row.username,
        "role": row.role,
        "is_active": row.is_active
    }


def get_all_users(db: Session):
    rows = db.execute(text("""
        SELECT id, username, email, kindle_email, role, is_active, created_at, last_login
        FROM users
        ORDER BY id ASC
    """)).fetchall()

    return [
        {
            "id": r.id,
            "username": r.username,
            "email": r.email,
            "kindle_email": r.kindle_email or "",
            "role": r.role,
            "is_active": r.is_active,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
            "last_login": r.last_login.strftime("%Y-%m-%d %H:%M") if r.last_login else "Never",
        }
        for r in rows
    ]


def update_user_profile(
    db: Session,
    user_id: int,
    email: str | None = None,
    kindle_email: str | None = None
) -> dict:
    """Update profile email and Kindle delivery email for user."""
    updates = []
    params = {"uid": user_id}

    if email is not None:
        clean_email = email.strip()
        updates.append("email = :email")
        params["email"] = clean_email if clean_email else None

    if kindle_email is not None:
        clean_kindle = kindle_email.strip()
        updates.append("kindle_email = :kindle_email")
        params["kindle_email"] = clean_kindle if clean_kindle else None

    if updates:
        sql = f"UPDATE users SET {', '.join(updates)} WHERE id = :uid"
        db.execute(text(sql), params)
        db.commit()

    return {"success": True, "message": "Profile updated successfully."}


def create_user(db: Session, username: str, password: str, role: str = "user", email: str | None = None):
    # Check if username exists
    existing = db.execute(text("SELECT id FROM users WHERE LOWER(username) = LOWER(:u)"), {"u": username.strip()}).fetchone()
    if existing:
        return {"error": "Username already exists"}

    if role not in ("admin", "power_user", "user"):
        role = "user"

    pwd_hash = hash_password(password)
    db.execute(text("""
        INSERT INTO users (username, email, password_hash, role, is_active)
        VALUES (:u, :e, :p, :r, TRUE)
    """), {"u": username.strip(), "e": email.strip() if email else None, "p": pwd_hash, "r": role})
    db.commit()
    return {"success": True}


def update_user(db: Session, user_id: int, role: str | None = None, is_active: bool | None = None, password: str | None = None, email: str | None = None):
    updates = []
    params = {"uid": user_id}

    if role in ("admin", "power_user", "user"):
        updates.append("role = :role")
        params["role"] = role

    if is_active is not None:
        updates.append("is_active = :active")
        params["active"] = is_active

    if password and password.strip():
        updates.append("password_hash = :pwd")
        params["pwd"] = hash_password(password.strip())

    if email is not None:
        updates.append("email = :email")
        params["email"] = email.strip() if email.strip() else None

    if updates:
        sql = f"UPDATE users SET {', '.join(updates)} WHERE id = :uid"
        db.execute(text(sql), params)
        db.commit()

    return {"success": True}


def delete_user(db: Session, user_id: int, current_user_id: int):
    if user_id == current_user_id:
        return {"error": "Cannot delete your own account"}

    # Ensure at least one admin remains
    admin_count = db.execute(text("SELECT count(*) FROM users WHERE role = 'admin' AND is_active = TRUE")).scalar() or 0
    target_role = db.execute(text("SELECT role FROM users WHERE id = :uid"), {"uid": user_id}).scalar()
    if target_role == "admin" and admin_count <= 1:
        return {"error": "Cannot delete the last active administrator"}

    db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user_id})
    db.commit()
    return {"success": True}


def change_user_password(db: Session, user_id: int, current_password: str, new_password: str) -> dict:
    """Allow any authenticated user to safely change their own password."""
    row = db.execute(text("SELECT password_hash FROM users WHERE id = :uid"), {"uid": user_id}).fetchone()
    if not row:
        return {"error": "User account not found"}

    if not verify_password(current_password, row.password_hash):
        return {"error": "Current password is incorrect"}

    clean_new = new_password.strip()
    if len(clean_new) < 6:
        return {"error": "New password must be at least 6 characters long"}

    new_hash = hash_password(clean_new)
    db.execute(text("UPDATE users SET password_hash = :p WHERE id = :uid"), {"p": new_hash, "uid": user_id})
    db.commit()
    return {"success": True}


def approve_user(db: Session, user_id: int) -> dict:
    """Approve and activate a pending user account."""
    row = db.execute(text("SELECT username, is_active FROM users WHERE id = :uid"), {"uid": user_id}).fetchone()
    if not row:
        return {"error": "User not found"}
    db.execute(text("UPDATE users SET is_active = TRUE WHERE id = :uid"), {"uid": user_id})
    db.commit()
    return {"success": True, "username": row.username}


def register_user(
    db: Session,
    username: str,
    password: str,
    confirm_password: str,
    email: str | None = None
) -> tuple[bool, str, dict | None]:
    """Public self-registration handler with approval workflow."""
    if not getattr(settings, "ALLOW_REGISTRATION", True):
        return False, "Public user registration is currently closed by the administrator.", None

    username = (username or "").strip()
    if len(username) < 3 or len(username) > 30:
        return False, "Username must be between 3 and 30 characters in length.", None

    if not re.match(r"^[a-zA-Z0-9_\-\.]{3,30}$", username):
        return False, "Username may only contain letters, numbers, dots, hyphens, and underscores.", None

    if email and email.strip():
        email = email.strip()
        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            return False, "Please enter a valid email address.", None
    else:
        email = None

    password = (password or "").strip()
    confirm_password = (confirm_password or "").strip()

    if len(password) < 6:
        return False, "Password must be at least 6 characters long.", None

    if password != confirm_password:
        return False, "Passwords do not match. Please verify and try again.", None

    # Check for duplicate username
    existing_user = db.execute(
        text("SELECT id FROM users WHERE LOWER(username) = LOWER(:u)"),
        {"u": username}
    ).fetchone()
    if existing_user:
        return False, "That username is already taken. Please choose another.", None

    # Check for duplicate email if provided
    if email:
        existing_email = db.execute(
            text("SELECT id FROM users WHERE LOWER(email) = LOWER(:e)"),
            {"e": email}
        ).fetchone()
        if existing_email:
            return False, "That email address is already associated with an existing account.", None

    # Determine activation status based on admin approval setting
    require_approval = getattr(settings, "REQUIRE_ADMIN_APPROVAL", True)
    is_active = not require_approval

    pwd_hash = hash_password(password)
    res = db.execute(text("""
        INSERT INTO users (username, email, password_hash, role, is_active)
        VALUES (:u, :e, :p, 'user', :active)
        RETURNING id
    """), {"u": username, "e": email, "p": pwd_hash, "active": is_active})
    new_user_id = res.scalar()
    db.commit()

    return True, "Registration successful.", {
        "user_id": new_user_id,
        "username": username,
        "is_active": is_active,
        "require_approval": require_approval
    }
