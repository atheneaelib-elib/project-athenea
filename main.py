import os
os.environ.setdefault("LANG", "C.UTF-8")
os.environ.setdefault("LC_ALL", "C.UTF-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import json
import urllib.parse
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from pydantic import BaseModel
from fastapi import FastAPI, Depends, Request, Response, HTTPException, Query, Form, File, UploadFile, Body, status
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import jinja2
from sqlalchemy.orm import Session
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
import starlette.formparsers

# Remove Starlette default 1000 file upload ceiling to allow large batch folder uploads
starlette.formparsers.MultiPartParser.max_files = 100000
starlette.formparsers.MultiPartParser.max_fields = 100000

logger = logging.getLogger("bookserver")

try:
    from .settings import settings
    from .db import get_db, SessionLocal
    from .services import books as book_service
    from .services import auth as auth_service
    from .services import importer as importer_service
    from .services import tts as tts_service
    from .services import opds as opds_service
    from .services import admin_settings as admin_settings_service
    from .services import setup as setup_service
    from .services import i18n as i18n_service
except (ImportError, ValueError):
    from settings import settings
    from db import get_db, SessionLocal
    from services import books as book_service
    from services import auth as auth_service
    from services import importer as importer_service
    from services import tts as tts_service
    from services import opds as opds_service
    from services import admin_settings as admin_settings_service
    from services import setup as setup_service
    from services import i18n as i18n_service

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
PLACEHOLDER_COVER = STATIC_DIR / "placeholder.svg"


import time
import threading
from collections import defaultdict


def assert_safe_path(target_path: Path, allowed_root: Path) -> Path:
    """Ensure target_path resolves strictly within allowed_root to prevent traversal."""
    try:
        resolved_root = allowed_root.resolve()
        resolved_target = target_path.resolve()
        if resolved_target == resolved_root or resolved_root in resolved_target.parents:
            return resolved_target
    except Exception:
        pass
    logger.warning(f"Security Alert: Path traversal attempt blocked: {target_path}")
    raise HTTPException(status_code=403, detail="Access denied: path outside allowed library boundary")


class LoginRateLimiter:
    """Thread-safe sliding-window rate limiter to prevent brute-force attacks on /login."""
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts = defaultdict(list)
        self.lock = threading.Lock()

    def get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        return request.client.host if request.client else "unknown"

    def is_rate_limited(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self.lock:
            self.attempts[key] = [t for t in self.attempts[key] if now - t < self.window_seconds]
            if len(self.attempts[key]) >= self.max_attempts:
                oldest = self.attempts[key][0]
                remaining = int(self.window_seconds - (now - oldest))
                return True, max(remaining, 1)
            return False, 0

    def record_failed_attempt(self, key: str):
        with self.lock:
            self.attempts[key].append(time.time())

    def reset(self, key: str):
        with self.lock:
            self.attempts.pop(key, None)


login_limiter = LoginRateLimiter(
    max_attempts=settings.LOGIN_RATE_LIMIT_ATTEMPTS,
    window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize auth tables and sequences if database is already configured
    try:
        db = SessionLocal()
        try:
            auth_service.init_auth_tables(db)
            importer_service.ensure_sequences(db)
            importer_service.convert_all_fb2_books(db)
        finally:
            db.close()
    except Exception as e:
        logger.info(f"Database not yet initialized (First-run setup wizard ready): {e}")
    yield


app = FastAPI(
    title="Athenea",
    description="High-performance eBook & Neural Audiobook Library powered by PostgreSQL and FastAPI",
    version="2.0.0",
    docs_url="/docs" if settings.ENABLE_DOCS else None,
    redoc_url="/redoc" if settings.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if settings.ENABLE_DOCS else None,
    lifespan=lifespan
)

# First-Run Setup Guard Middleware
@app.middleware("http")
async def setup_guard_middleware(request: Request, call_next):
    path = request.url.path
    # Allow static assets, favicon, /health, and /setup endpoints to pass without redirection
    if path.startswith("/static") or path == "/favicon.ico" or path.startswith("/setup") or path == "/health":
        return await call_next(request)

    # If the system is not yet initialized with an admin user, redirect to /setup
    if not setup_service.is_system_configured():
        return RedirectResponse(url="/setup", status_code=status.HTTP_302_FOUND)

    return await call_next(request)

# Static files mount
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_library_total_books() -> str:
    """Retrieve formatted total books count from cache or database."""
    try:
        with SessionLocal() as db:
            stats = book_service.get_stats(db)
            return f"{stats.get('books', 0):,}"
    except Exception as e:
        logger.warning(f"Failed to fetch total books count for template: {e}")
        return ""


@jinja2.pass_context
def jinja_t(context, key: str, **kwargs):
    request = context.get("request")
    lang = context.get("lang") or (i18n_service.get_request_language(request) if request else "en")
    return i18n_service.t(key, lang=lang, **kwargs)


@jinja2.pass_context
def jinja_get_lang(context):
    request = context.get("request")
    return context.get("lang") or (i18n_service.get_request_language(request) if request else "en")


@jinja2.pass_context
def jinja_get_lang_obj(context):
    request = context.get("request")
    code = context.get("lang") or (i18n_service.get_request_language(request) if request else "en")
    return i18n_service.get_language_meta(code)


templates.env.globals["get_library_total_books"] = get_library_total_books
templates.env.globals["t"] = jinja_t
templates.env.globals["_"] = jinja_t
templates.env.globals["get_current_lang"] = jinja_get_lang
templates.env.globals["get_current_lang_obj"] = jinja_get_lang_obj
templates.env.globals["SUPPORTED_LANGUAGES"] = i18n_service.SUPPORTED_LANGUAGES


# ===================================================================
# DEFENSIVE SECURITY MIDDLEWARES
# ===================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces essential defensive HTTP security headers on all responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


class RequireLoginMiddleware(BaseHTTPMiddleware):
    """Enforce login-wall, OPDS Basic Auth, and admin-only paths on every request."""

    PUBLIC_PREFIXES    = ("/login", "/register", "/static", "/api/tts", "/api/set-language", "/manifest.json", "/sw.js", "/cover/", "/thumbnail/", "/setup", "/health")
    ADMIN_ONLY_PATHS   = ("/docs", "/redoc", "/openapi.json", "/api/stats", "/admin")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # ── Setup Guard: Unconfigured instances direct all non-static traffic to /setup ──
        if not setup_service.is_system_configured():
            if path.startswith("/static") or path == "/favicon.ico" or path.startswith("/setup") or path == "/health":
                return await call_next(request)
            return RedirectResponse(url="/setup", status_code=302)

        # ── Tier 1: fully public ────────────────────────────────────
        if any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)

        # ── Resolve session once for tiers 2 & 3 ───────────────────
        session_id = request.cookies.get(auth_service.SESSION_COOKIE_NAME)
        user = None
        if session_id:
            db = SessionLocal()
            try:
                user = auth_service.get_user_from_session(db, session_id)
            finally:
                db.close()

        # ── HTTP Basic Auth check (for OPDS e-readers or automated clients) ─
        if not user and "authorization" in request.headers:
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Basic "):
                try:
                    import base64
                    creds = base64.b64decode(auth_header[6:]).decode("utf-8").split(":", 1)
                    if len(creds) == 2:
                        db = SessionLocal()
                        try:
                            auth_res = auth_service.authenticate_user(db, creds[0], creds[1])
                            if auth_res:
                                user = auth_res
                                request.state.basic_auth_user = user
                        finally:
                            db.close()
                except Exception:
                    pass

        # ── OPDS Authentication handling ────────────────────────────
        if path.startswith("/opds"):
            if not user:
                from starlette.responses import Response as StarletteResponse
                return StarletteResponse(
                    status_code=401,
                    content="Authentication required for OPDS catalog",
                    headers={"WWW-Authenticate": 'Basic realm="eBook Library OPDS"'}
                )
            return await call_next(request)

        # ── Tier 2: admin-only paths ─────────────────────────────────
        if any(path.startswith(p) for p in self.ADMIN_ONLY_PATHS):
            if not user or not user.get("is_admin"):
                # Logged-in non-admins → home; guests → login
                if user:
                    return RedirectResponse(url="/", status_code=302)
                return RedirectResponse(
                    url=f"/login?next={urllib.parse.quote(path)}", status_code=302
                )
            return await call_next(request)

        # ── Tier 3: any authenticated user ───────────────────────────
        if not user:
            login_url = f"/login?next={urllib.parse.quote(path)}"
            # HTMX requests get a header redirect so partials don't break
            if request.headers.get("HX-Request"):
                from starlette.responses import Response as StarletteResponse
                return StarletteResponse(
                    status_code=200,
                    headers={"HX-Redirect": login_url}
                )
            return RedirectResponse(url=login_url, status_code=302)

        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequireLoginMiddleware)

# MIME type mapping
MIME_TYPES = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "mobi": "application/x-mobipocket-ebook",
    "azw3": "application/vnd.amazon.ebook",
    "fb2": "application/x-fictionbook+xml",
    "txt": "text/plain",
    "djvu": "image/vnd.djvu",
    "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar",
}


def parse_int_param(v: str | int | None) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    v = str(v).strip()
    if not v:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)):
    if hasattr(request.state, "basic_auth_user"):
        return request.state.basic_auth_user
    session_id = request.cookies.get(auth_service.SESSION_COOKIE_NAME)
    return auth_service.get_user_from_session(db, session_id)


def require_admin(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


# ===================================================================
# AUTHENTICATION ROUTES
# ===================================================================


def _safe_next(next_url: str) -> str:
    """Sanitize the post-login redirect — only allow relative UI paths."""
    if (
        next_url
        and next_url.startswith("/")
        and not next_url.startswith("/api/")
        and not next_url.startswith("/docs")
        and not next_url.startswith("/openapi")
    ):
        return next_url
    return "/"


# ===================================================================
# FIRST-RUN SETUP WIZARD ROUTES
# ===================================================================

class TestDbRequest(BaseModel):
    host: str
    port: int = 5432
    dbname: str
    user: str
    password: str


class CompleteSetupRequest(BaseModel):
    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str
    library_root: str
    admin_username: str
    admin_password: str
    admin_email: str = ""
    allow_registration: bool = True
    require_approval: bool = True


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    if setup_service.is_system_configured():
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    current_lang = i18n_service.get_request_language(request)
    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={
            "request": request,
            "current_lang": current_lang,
            "translations_json": i18n_service.get_translations_json_for_client(),
            "supported_languages": i18n_service.SUPPORTED_LANGUAGES,
            "default_db_host": settings.DB_HOST or "localhost",
            "default_db_port": settings.DB_PORT or 5432,
            "default_db_name": settings.DB_NAME or "athenea",
            "default_db_user": settings.DB_USER or "athenea",
            "default_db_password": settings.DB_PASSWORD or "",
            "default_library_root": settings.LIBRARY_ROOT or "/data/books",
        }
    )


@app.get("/api/set-language")
def set_ui_language(lang: str = "en", next: str = "/"):
    """Switch user interface language and persist preference in a 1-year cookie."""
    valid_lang = lang.lower() if lang.lower() in i18n_service.SUPPORTED_CODES else i18n_service.DEFAULT_LANGUAGE
    safe_next = next if (next and next.startswith("/") and not next.startswith("//")) else "/"
    resp = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        key=i18n_service.COOKIE_NAME,
        value=valid_lang,
        max_age=365 * 24 * 3600,
        httponly=False,
        samesite="lax",
        path="/"
    )
    return resp


@app.post("/setup/test-db")
def setup_test_db(req: TestDbRequest):
    if setup_service.is_system_configured():
        raise HTTPException(status_code=403, detail="System is already configured")

    success, msg = setup_service.test_database_connection(
        host=req.host,
        port=req.port,
        dbname=req.dbname,
        user=req.user,
        password=req.password
    )
    return {"success": success, "message": msg}


@app.post("/setup/complete")
def setup_complete(req: CompleteSetupRequest):
    if setup_service.is_system_configured():
        raise HTTPException(status_code=403, detail="System is already configured")

    try:
        success, msg = setup_service.complete_initial_setup(
            db_host=req.db_host,
            db_port=req.db_port,
            db_name=req.db_name,
            db_user=req.db_user,
            db_password=req.db_password,
            library_root=req.library_root,
            admin_username=req.admin_username,
            admin_password=req.admin_password,
            admin_email=req.admin_email,
            allow_registration=req.allow_registration,
            require_approval=req.require_approval
        )
        if not success:
            return {"success": False, "error": msg}

        return {"success": True, "message": msg, "redirect": "/login?setup=success"}
    except Exception as exc:
        logger.exception("Setup completion failed with unhandled exception")
        return {"success": False, "error": f"Setup error: {str(exc)}"}


@app.get("/health")
def health_check():
    """Service health and setup status check."""
    return {
        "status": "healthy",
        "app": "Athenea",
        "version": "2.0.0",
        "configured": setup_service.is_system_configured()
    }


# ===================================================================
# AUTHENTICATION & LOGIN ROUTES
# ===================================================================

@app.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    next: str = "/",
    error: str | None = None,
    msg: str | None = None,
    setup: str | None = None,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    safe_next = _safe_next(next)
    if user:
        return RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)

    display_msg = msg
    if setup == "success" and not display_msg:
        display_msg = "Athenea setup completed successfully! Please sign in with your administrator account."

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next_url": safe_next,
            "error": error,
            "msg": display_msg,
            "allow_registration": getattr(settings, "ALLOW_REGISTRATION", True)
        }
    )


@app.post("/login")
def login_action(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db)
):
    safe_next = _safe_next(next)
    client_ip = login_limiter.get_client_ip(request)

    # Multi-worker rate limiting check: IP and username brute-force protection
    is_limited, wait_sec = auth_service.check_login_rate_limit(
        db, client_ip, username,
        max_attempts=settings.LOGIN_RATE_LIMIT_ATTEMPTS,
        window_seconds=settings.LOGIN_RATE_LIMIT_WINDOW
    )

    if is_limited:
        logger.warning(f"Login rate limit active for IP: {client_ip}, user: {username}")
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_url": safe_next,
                "error": f"Too many failed login attempts. Please wait {wait_sec} seconds before trying again.",
                "allow_registration": getattr(settings, "ALLOW_REGISTRATION", True)
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS
        )

    user = auth_service.authenticate_user(db, username, password)
    if user is False:
        # User exists and password is correct, but account is inactive / pending approval
        logger.warning(f"Login rejected for inactive/pending user '{username}' from IP {client_ip}")
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_url": safe_next,
                "error": "Your account is awaiting administrator approval or is currently disabled. Please contact an administrator.",
                "allow_registration": getattr(settings, "ALLOW_REGISTRATION", True)
            }
        )

    if not user:
        logger.warning(f"Login failed: user '{username}' from IP {client_ip}")
        auth_service.record_failed_login(db, client_ip, username)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_url": safe_next,
                "error": "Invalid username or password",
                "allow_registration": getattr(settings, "ALLOW_REGISTRATION", True)
            }
        )

    # Login successful: clear failure counts
    auth_service.clear_failed_logins(db, client_ip, username)

    session_id = auth_service.create_session(db, user["id"])
    redirect = RedirectResponse(url=safe_next, status_code=status.HTTP_302_FOUND)

    # Set secure cookie when on HTTPS (direct or forwarded by reverse proxy)
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
        or settings.COOKIE_SECURE
    )
    redirect.set_cookie(
        key=auth_service.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=is_https,
        samesite="lax",
    )
    return redirect


# ===================================================================
# SELF-REGISTRATION ROUTES
# ===================================================================

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "allow_registration": getattr(settings, "ALLOW_REGISTRATION", True),
            "require_approval": getattr(settings, "REQUIRE_ADMIN_APPROVAL", True),
            "error": None,
            "registered_pending": False
        }
    )


@app.post("/register", response_class=HTMLResponse)
def register_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    email: str | None = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    success, message, reg_data = auth_service.register_user(
        db=db,
        username=username,
        password=password,
        confirm_password=confirm_password,
        email=email
    )

    if not success:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "allow_registration": getattr(settings, "ALLOW_REGISTRATION", True),
                "require_approval": getattr(settings, "REQUIRE_ADMIN_APPROVAL", True),
                "username": username,
                "email": email or "",
                "error": message,
                "registered_pending": False
            },
            status_code=400
        )

    # If account is auto-approved, log in immediately
    if reg_data and reg_data.get("is_active"):
        session_id = auth_service.create_session(db, reg_data["user_id"])
        redirect = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        is_https = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
            or settings.COOKIE_SECURE
        )
        redirect.set_cookie(
            key=auth_service.SESSION_COOKIE_NAME,
            value=session_id,
            max_age=30 * 24 * 3600,
            httponly=True,
            secure=is_https,
            samesite="lax",
        )
        return redirect

    # Admin approval required: show friendly pending approval card
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "allow_registration": True,
            "require_approval": True,
            "registered_pending": True,
            "username": username
        }
    )


@app.get("/logout")
def logout_action(request: Request, response: Response, db: Session = Depends(get_db)):
    session_id = request.cookies.get(auth_service.SESSION_COOKIE_NAME)
    if session_id:
        auth_service.delete_session(db, session_id)

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    redirect.delete_cookie(key=auth_service.SESSION_COOKIE_NAME)
    return redirect


# ===================================================================
# ADMIN USER MANAGEMENT ROUTES
# ===================================================================

@app.get("/admin/users", response_class=HTMLResponse)
def admin_users_page(request: Request, db: Session = Depends(get_db)):
    return RedirectResponse(url="/admin/settings?tab=users", status_code=status.HTTP_302_FOUND)


# ===================================================================
# ADMIN SYSTEM, USERS & MAINTENANCE ROUTES
# ===================================================================

@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings_page(
    request: Request,
    tab: str = Query("users"),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user["is_admin"]:
        return RedirectResponse(url=f"/login?next={urllib.parse.quote(str(request.url))}", status_code=status.HTTP_302_FOUND)

    users_list = auth_service.get_all_users(db)
    user_stats = {
        "total": len(users_list),
        "admins": sum(1 for u in users_list if u["role"] == "admin"),
        "power_users": sum(1 for u in users_list if u["role"] == "power_user"),
        "standard_users": sum(1 for u in users_list if u["role"] == "user"),
    }

    system_info = admin_settings_service.get_system_status(db)
    smtp_configured = bool(settings.SMTP_HOST and settings.SMTP_HOST.strip())
    smtp_password_set = bool(settings.SMTP_PASSWORD and settings.SMTP_PASSWORD.strip())

    total_books = system_info.get("total_books")
    if not total_books:
        stats = book_service.get_stats(db)
        total_books = stats.get("books", 0)

    return templates.TemplateResponse(
        request=request,
        name="admin/settings.html",
        context={
            "settings": settings,
            "users": users_list,
            "user_stats": user_stats,
            "system_info": system_info,
            "smtp_configured": smtp_configured,
            "smtp_password_set": smtp_password_set,
            "active_tab": tab,
            "current_user": user,
            "total_books_count": f"{total_books:,}",
        }
    )


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    email: str | None = None


class UpdateUserRequest(BaseModel):
    user_id: int
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None
    email: str | None = None


@app.post("/admin/users/create")
def admin_create_user(
    req: CreateUserRequest,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    res = auth_service.create_user(db, req.username, req.password, req.role, req.email)
    return res


@app.post("/admin/users/update")
def admin_update_user(
    req: UpdateUserRequest,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    res = auth_service.update_user(db, req.user_id, req.role, req.is_active, req.password, req.email)
    return res


@app.post("/admin/users/delete/{user_id}")
def admin_delete_user(
    user_id: int,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    res = auth_service.delete_user(db, user_id, admin["id"])
    return res


@app.post("/admin/users/approve/{user_id}")
def admin_approve_user(
    user_id: int,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    res = auth_service.approve_user(db, user_id)
    return res


class SaveRegistrationSettingsRequest(BaseModel):
    allow_registration: bool
    require_approval: bool


@app.post("/admin/settings/registration")
def admin_save_registration_settings(
    req: SaveRegistrationSettingsRequest,
    admin: dict = Depends(require_admin)
):
    res = admin_settings_service.save_registration_settings(
        allow_registration=req.allow_registration,
        require_approval=req.require_approval
    )
    return res


class SaveBooksPathRequest(BaseModel):
    library_root: str


@app.post("/admin/settings/books-path")
def admin_save_books_path(
    req: SaveBooksPathRequest,
    admin: dict = Depends(require_admin)
):
    try:
        res = admin_settings_service.save_library_path(req.library_root)
        status_code = 200 if res.get("success") else 400
        return JSONResponse(status_code=status_code, content=res)
    except Exception as e:
        logger.error(f"Error saving books path: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


@app.post("/admin/settings/books-path/verify")
def admin_verify_books_path(
    req: SaveBooksPathRequest,
    admin: dict = Depends(require_admin)
):
    try:
        res = admin_settings_service.verify_books_path(req.library_root)
        status_code = 200 if res.get("success") else 400
        return JSONResponse(status_code=status_code, content=res)
    except Exception as e:
        logger.error(f"Error verifying books path: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


# ===================================================================
# ADMIN MAINTENANCE & DIAGNOSTIC ROUTES
# ===================================================================

class UnbanIPRequest(BaseModel):
    ip: str


@app.post("/admin/maintenance/unban")
def admin_maintenance_unban(
    req: UnbanIPRequest,
    admin: dict = Depends(require_admin)
):
    res = admin_settings_service.unban_ip(req.ip)
    return res


@app.post("/admin/maintenance/backup")
def admin_maintenance_backup(
    admin: dict = Depends(require_admin)
):
    res = admin_settings_service.trigger_manual_backup()
    return res


@app.post("/admin/maintenance/clear-thumbs")
def admin_maintenance_clear_thumbs(
    admin: dict = Depends(require_admin)
):
    res = admin_settings_service.clear_thumbnail_cache()
    return res


@app.post("/admin/maintenance/flush-cache")
def admin_maintenance_flush_cache(
    admin: dict = Depends(require_admin)
):
    res = admin_settings_service.flush_query_cache()
    return res


@app.post("/admin/maintenance/vacuum")
def admin_maintenance_vacuum(
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    res = admin_settings_service.vacuum_database(db)
    return res


@app.get("/admin/backup/download/{filename}")
def admin_download_backup(
    filename: str,
    admin: dict = Depends(require_admin)
):
    target = admin_settings_service.get_backup_file_path(filename)
    if not target:
        raise HTTPException(status_code=404, detail="Backup file not found or invalid filename.")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/gzip"
    )


class SyncLibraryRequest(BaseModel):
    dry_run: bool = False


@app.post("/admin/maintenance/sync-library")
def admin_sync_library(
    req: SyncLibraryRequest = Body(default=SyncLibraryRequest()),
    admin: dict = Depends(require_admin)
):
    return admin_settings_service.start_library_sync(dry_run=req.dry_run)


@app.get("/admin/maintenance/sync-status")
def admin_sync_status(
    admin: dict = Depends(require_admin)
):
    return admin_settings_service.get_library_sync_status()


@app.post("/admin/maintenance/audit-files")
def admin_audit_files(
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    return admin_settings_service.audit_library_files(db=db, sample_size=150)


class SaveSMTPSettingsRequest(BaseModel):
    host: str
    port: int = 587
    user: str = ""
    password: str = ""
    from_email: str = getattr(settings, "SMTP_FROM", "books@example.com")
    tls: bool = True
    ssl: bool = False


@app.post("/admin/settings/smtp")
def admin_save_smtp_settings(
    req: SaveSMTPSettingsRequest,
    admin: dict = Depends(require_admin)
):
    try:
        res = admin_settings_service.save_smtp_settings(
            host=req.host,
            port=req.port,
            user=req.user,
            password=req.password,
            from_addr=req.from_email,
            tls=req.tls,
            ssl=req.ssl
        )
        return res
    except Exception as e:
        logger.error(f"Error saving SMTP settings: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


class TestSMTPSettingsRequest(BaseModel):
    host: str
    port: int = 587
    user: str = ""
    password: str = ""
    from_email: str = getattr(settings, "SMTP_FROM", "books@example.com")
    tls: bool = True
    ssl: bool = False
    recipient: str = ""


@app.post("/admin/settings/smtp/test")
def admin_test_smtp_settings(
    req: TestSMTPSettingsRequest,
    admin: dict = Depends(require_admin)
):
    try:
        res = admin_settings_service.test_smtp_connection(
            host=req.host,
            port=req.port,
            user=req.user,
            password=req.password,
            from_addr=req.from_email,
            tls=req.tls,
            ssl=req.ssl,
            test_recipient=req.recipient
        )
        status_code = 200 if res.get("success") else 400
        return JSONResponse(status_code=status_code, content=res)
    except Exception as e:
        logger.error(f"Error testing SMTP connection: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": str(e)})


# ===================================================================
# USER SELF-SERVICE PROFILE & PASSWORD CHANGE
# ===================================================================

@app.get("/profile/password-modal", response_class=HTMLResponse)
def profile_password_modal(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return templates.TemplateResponse(
        request=request,
        name="partials/change_password_modal.html",
        context={"current_user": user}
    )


@app.post("/profile/change-password", response_class=HTMLResponse)
def profile_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if new_password != confirm_password:
        return HTMLResponse("""
            <div class="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs font-medium flex items-center gap-2">
              <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
              <span>New passwords do not match.</span>
            </div>
        """)

    res = auth_service.change_user_password(
        db=db,
        user_id=user["id"],
        current_password=current_password,
        new_password=new_password
    )

    if "error" in res:
        return HTMLResponse(f"""
            <div class="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs font-medium flex items-center gap-2">
              <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
              <span>{res['error']}</span>
            </div>
        """)

    return HTMLResponse("""
        <div class="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <svg class="w-4 h-4 flex-shrink-0 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
            <span>Password updated successfully!</span>
          </div>
          <script>
            setTimeout(() => { closePasswordModal(); }, 1600);
          </script>
        </div>
    """)


@app.post("/profile/update", response_class=HTMLResponse)
def profile_update(
    request: Request,
    email: str = Form(default=""),
    kindle_email: str = Form(default=""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    res = auth_service.update_user_profile(
        db=db,
        user_id=user["id"],
        email=email,
        kindle_email=kindle_email
    )

    clean_kindle = kindle_email.strip()
    return HTMLResponse(f"""
        <div class="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <svg class="w-4 h-4 flex-shrink-0 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>
            <span>Profile delivery settings saved!</span>
          </div>
          <script>
            if (localStorage) localStorage.setItem('kindle_email_pref', '{clean_kindle}');
            setTimeout(() => {{ closePasswordModal(); }}, 1500);
          </script>
        </div>
    """)


@app.get("/books/random/modal", response_class=HTMLResponse)
def random_book_modal(
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    book_id = db.execute(text("""
        SELECT id FROM books 
        WHERE id >= (SELECT floor(random() * (SELECT max(id) FROM books))::int) 
        ORDER BY id ASC LIMIT 1
    """)).scalar()

    if not book_id:
        raise HTTPException(status_code=404, detail="No books found")

    user_id = current_user["id"] if current_user else None
    book = book_service.get_book_details(db, book_id, user_id=user_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    return templates.TemplateResponse(
        request=request,
        name="partials/book_modal.html",
        context={
            "book": book,
            "current_user": current_user,
        }
    )



# ===================================================================
# WEB UI ROUTES
# ===================================================================

@app.get("/", response_class=HTMLResponse)
def index_page(
    request: Request,
    q: str | None = None,
    favorites: str | None = None,
    shelf: str | None = None,
    search_desc: str | None = None,
    author_id: str | None = None,
    series_id: str | None = None,
    tag_id: str | None = None,
    format_name: str | None = None,
    language_id: str | None = None,
    sort_by: str = "recent",
    view_mode: str | None = None,
    page: str | int = 1,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    user_id = current_user["id"] if current_user else None
    fav_only = bool(favorites and str(favorites).strip() in ("1", "true", "True"))
    search_desc_bool = bool(search_desc and str(search_desc).strip() in ("1", "true", "True", "on"))
    aid = parse_int_param(author_id)
    sid = parse_int_param(series_id)
    tid = parse_int_param(tag_id)
    lid = parse_int_param(language_id)
    p = parse_int_param(page) or 1

    sidebar_data = book_service.get_sidebar_data(db)
    fav_count = book_service.get_user_favorites_count(db, user_id)
    shelf_counts = book_service.get_user_shelf_counts(db, user_id)

    books_data = book_service.get_books(
        db=db,
        page=p,
        size=settings.PAGE_SIZE,
        q=q,
        author_id=aid,
        series_id=sid,
        tag_id=tid,
        format_name=format_name,
        language_id=lid,
        sort_by=sort_by,
        favorites_only=fav_only,
        reading_status=shelf,
        search_descriptions=search_desc_bool,
        user_id=user_id,
        view_mode=view_mode,
    )

    # Active filter label
    active_filter_label = None
    if fav_only:
        active_filter_label = "Favorites: My Saved Books"
    elif shelf == "reading":
        active_filter_label = "Shelf: Currently Reading"
    elif shelf == "plan_to_read":
        active_filter_label = "Shelf: Want to Read"
    elif shelf == "finished":
        active_filter_label = "Shelf: Finished Books"
    elif aid:
        author_name = db.execute(text("SELECT name FROM authors WHERE id = :id"), {"id": aid}).scalar()
        if author_name:
            active_filter_label = f"Author: {author_name}"
    elif sid:
        series_name = db.execute(text("SELECT name FROM series WHERE id = :id"), {"id": sid}).scalar()
        if series_name:
            active_filter_label = f"Series: {series_name}"
    elif tid:
        tag_name = db.execute(text("SELECT name FROM tags WHERE id = :id"), {"id": tid}).scalar()
        if tag_name:
            active_filter_label = f"Genre: {tag_name}"
    elif format_name:
        active_filter_label = f"Format: {format_name.upper()}"
    elif lid:
        lang_code = db.execute(text("SELECT lang_code FROM languages WHERE id = :id"), {"id": lid}).scalar()
        if lang_code:
            active_filter_label = f"Language: {book_service.format_language_name(lang_code)}"

    stats = book_service.get_stats(db)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "books": books_data["books"],
            "grouped_authors": books_data["grouped_authors"],
            "view_mode": books_data["view_mode"],
            "total": f"{books_data['total']:,}",
            "page": books_data["page"],
            "total_pages": books_data["total_pages"],
            "sidebar": sidebar_data,
            "active_query": q,
            "active_filter_label": active_filter_label,
            "total_books_count": f"{stats['books']:,}",
            "current_author_id": aid or "",
            "current_series_id": sid or "",
            "current_tag_id": tid or "",
            "current_format_name": format_name or "",
            "current_favorites_only": fav_only,
            "current_shelf": shelf or "",
            "favorites_count": fav_count,
            "shelf_counts": shelf_counts,
            "search_desc": search_desc_bool,
            "current_view_mode": books_data["view_mode"],
            "current_page": p,
            "current_user": current_user,
        }
    )


@app.get("/books/grid", response_class=HTMLResponse)
def books_grid_partial(
    request: Request,
    q: str | None = None,
    favorites: str | None = None,
    shelf: str | None = None,
    search_desc: str | None = None,
    author_id: str | None = None,
    series_id: str | None = None,
    tag_id: str | None = None,
    format_name: str | None = None,
    language_id: str | None = None,
    sort_by: str = "recent",
    view_mode: str | None = None,
    page: str | int = 1,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    user_id = current_user["id"] if current_user else None
    fav_only = bool(favorites and str(favorites).strip() in ("1", "true", "True"))
    search_desc_bool = bool(search_desc and str(search_desc).strip() in ("1", "true", "True", "on"))
    aid = parse_int_param(author_id)
    sid = parse_int_param(series_id)
    tid = parse_int_param(tag_id)
    lid = parse_int_param(language_id)
    p = parse_int_param(page) or 1

    books_data = book_service.get_books(
        db=db,
        page=p,
        size=settings.PAGE_SIZE,
        q=q,
        author_id=aid,
        series_id=sid,
        tag_id=tid,
        format_name=format_name,
        language_id=lid,
        sort_by=sort_by,
        favorites_only=fav_only,
        reading_status=shelf,
        search_descriptions=search_desc_bool,
        user_id=user_id,
        view_mode=view_mode,
    )

    active_filter_label = None
    if fav_only:
        active_filter_label = "Favorites: My Saved Books"
    elif shelf == "reading":
        active_filter_label = "Shelf: Currently Reading"
    elif shelf == "plan_to_read":
        active_filter_label = "Shelf: Want to Read"
    elif shelf == "finished":
        active_filter_label = "Shelf: Finished Books"
    elif aid:
        author_name = db.execute(text("SELECT name FROM authors WHERE id = :id"), {"id": aid}).scalar()
        if author_name:
            active_filter_label = f"Author: {author_name}"
    elif sid:
        series_name = db.execute(text("SELECT name FROM series WHERE id = :id"), {"id": sid}).scalar()
        if series_name:
            active_filter_label = f"Series: {series_name}"
    elif tid:
        tag_name = db.execute(text("SELECT name FROM tags WHERE id = :id"), {"id": tid}).scalar()
        if tag_name:
            active_filter_label = f"Genre: {tag_name}"
    elif format_name:
        active_filter_label = f"Format: {format_name.upper()}"
    elif lid:
        lang_code = db.execute(text("SELECT lang_code FROM languages WHERE id = :id"), {"id": lid}).scalar()
        if lang_code:
            active_filter_label = f"Language: {book_service.format_language_name(lang_code)}"

    return templates.TemplateResponse(
        request=request,
        name="partials/book_grid.html",
        context={
            "books": books_data["books"],
            "grouped_authors": books_data["grouped_authors"],
            "view_mode": books_data["view_mode"],
            "total": f"{books_data['total']:,}",
            "page": books_data["page"],
            "total_pages": books_data["total_pages"],
            "active_query": q,
            "active_filter_label": active_filter_label,
            "current_favorites_only": fav_only,
            "current_shelf": shelf or "",
            "current_user": current_user,
        }
    )


@app.get("/book/{book_id}/modal", response_class=HTMLResponse)
def book_modal_partial(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    user_id = current_user["id"] if current_user else None
    book = book_service.get_book_details(db, book_id, user_id=user_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    return templates.TemplateResponse(
        request=request,
        name="partials/book_modal.html",
        context={
            "book": book,
            "current_user": current_user
        }
    )


@app.post("/book/{book_id}/favorite", response_class=HTMLResponse)
def toggle_book_favorite(
    request: Request,
    book_id: int,
    context: str = "card",
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")

    is_fav = book_service.toggle_favorite(db, current_user["id"], book_id)
    fav_count = book_service.get_user_favorites_count(db, current_user["id"])

    response = templates.TemplateResponse(
        request=request,
        name="partials/favorite_btn.html",
        context={
            "book_id": book_id,
            "is_favorite": is_fav,
            "context": context,
            "count": fav_count,
        }
    )
    # Include HX-Trigger header for client-side events if needed
    response.headers["HX-Trigger"] = json.dumps({
        "favoriteChanged": {
            "bookId": book_id,
            "isFavorite": is_fav,
            "count": fav_count
        }
    })
    return response


# ===================================================================
# BOOK UPLOAD & INGESTION ROUTES (POWER USERS & ADMINS)
# ===================================================================

@app.get("/upload/modal", response_class=HTMLResponse)
def upload_modal_view(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user or not user.get("is_power_user"):
        raise HTTPException(
            status_code=403,
            detail="Permission denied. Adding books is restricted to Power Users and Administrators."
        )
    return templates.TemplateResponse(
        request=request,
        name="partials/upload_modal.html",
        context={"current_user": user}
    )


@app.post("/upload/process", response_class=HTMLResponse)
async def process_book_upload(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    folder_files: list[UploadFile] = File(default=[]),
    server_path: str = Form(default=""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.get("is_power_user"):
        raise HTTPException(status_code=403, detail="Permission denied")

    all_uploads = [f for f in (files + folder_files) if f and f.filename]
    logger.info(f"[/upload/process] files={len(files)}, folder_files={len(folder_files)}, server_path='{server_path}', valid_uploads={len(all_uploads)}")

    imported = []
    failed = []

    # 1. Process uploaded files
    if all_uploads:
        import tempfile
        import shutil

        temp_dir = Path(tempfile.mkdtemp(prefix="web_upload_"))
        try:
            for upload in all_uploads:
                orig_filename = Path(upload.filename).name
                ext = Path(orig_filename).suffix.lower()
                if ext not in (".epub", ".fb2", ".fbz", ".pdf", ".mobi", ".azw3", ".zip") and not orig_filename.lower().endswith(".fb2.zip"):
                    continue

                tmp_file = temp_dir / orig_filename
                with open(tmp_file, "wb") as out_f:
                    shutil.copyfileobj(upload.file, out_f)

                try:
                    res = importer_service.import_book_file(db, tmp_file, orig_filename)
                    imported.append(res)
                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    logger.exception(f"Error importing {orig_filename}: {e}\n{tb}")
                    failed.append({"file": orig_filename, "error": f"{str(e)} (Traceback: {tb.strip().splitlines()[-2] if len(tb.strip().splitlines()) >= 2 else ''})"})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # 2. Process server path if specified
    if server_path and server_path.strip():
        scan_dir = Path(server_path.strip()).resolve()
        lib_root = Path(settings.LIBRARY_ROOT).resolve()
        allowed_roots = [lib_root]
        if lib_root.parent.exists():
            allowed_roots.append(lib_root.parent)
        if not any(r in scan_dir.parents or scan_dir == r for r in allowed_roots):
            failed.append({"file": server_path, "error": f"Access denied: server path must be within {lib_root}"})
        else:
            logger.info(f"[/upload/process] Scanning server_path: {scan_dir}")
            scan_res = importer_service.scan_and_import_directory(db, scan_dir)
            if "error" in scan_res:
                failed.append({"file": server_path, "error": scan_res["error"]})
            else:
                imported.extend(scan_res.get("imported", []))
                failed.extend(scan_res.get("failed", []))

    if imported:
        book_service.invalidate_caches()

    return templates.TemplateResponse(
        request=request,
        name="partials/upload_result.html",
        context={
            "imported": imported,
            "failed": failed,
            "current_user": user,
        }
    )


@app.post("/upload/summary", response_class=HTMLResponse)
async def upload_summary_partial(
    request: Request,
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.get("is_power_user"):
        raise HTTPException(status_code=403, detail="Permission denied")

    imported = payload.get("imported", [])
    failed = payload.get("failed", [])
    logger.info(f"[/upload/summary] Rendering summary: imported={len(imported)}, failed={len(failed)}")

    return templates.TemplateResponse(
        request=request,
        name="partials/upload_result.html",
        context={
            "imported": imported,
            "failed": failed,
            "current_user": user,
        }
    )


@app.post("/api/books/upload")
async def api_upload_books(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    folder_files: list[UploadFile] = File(default=[]),
    server_path: str = Form(default=""),
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.get("is_power_user"):
        raise HTTPException(status_code=403, detail="Permission denied")

    all_uploads = [f for f in (files + folder_files) if f and f.filename]
    imported = []
    failed = []
    if all_uploads:
        import tempfile
        import shutil
        temp_dir = Path(tempfile.mkdtemp(prefix="api_upload_"))
        try:
            for upload in all_uploads:
                orig_filename = Path(upload.filename).name
                ext = Path(orig_filename).suffix.lower()
                if ext not in (".epub", ".fb2", ".fbz", ".pdf", ".mobi", ".azw3", ".zip") and not orig_filename.lower().endswith(".fb2.zip"):
                    continue
                tmp_file = temp_dir / orig_filename
                with open(tmp_file, "wb") as out_f:
                    shutil.copyfileobj(upload.file, out_f)
                try:
                    res = importer_service.import_book_file(db, tmp_file, orig_filename)
                    imported.append(res)
                except Exception as e:
                    import traceback
                    tb = traceback.format_exc()
                    logger.exception(f"Error importing {orig_filename}: {e}\n{tb}")
                    failed.append({"file": orig_filename, "error": f"{str(e)} (Traceback: {tb.strip().splitlines()[-2] if len(tb.strip().splitlines()) >= 2 else ''})"})
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if server_path and server_path.strip():
        scan_dir = Path(server_path.strip()).resolve()
        lib_root = Path(settings.LIBRARY_ROOT).resolve()
        allowed_roots = [lib_root]
        if lib_root.parent.exists():
            allowed_roots.append(lib_root.parent)
        if not any(r in scan_dir.parents or scan_dir == r for r in allowed_roots):
            failed.append({"file": server_path, "error": f"Access denied: server path must be within {lib_root}"})
        else:
            scan_res = importer_service.scan_and_import_directory(db, scan_dir)
            if "error" in scan_res:
                failed.append({"file": server_path, "error": scan_res["error"]})
            else:
                imported.extend(scan_res.get("imported", []))
                failed.extend(scan_res.get("failed", []))

    if imported:
        book_service.invalidate_caches()

    return {"imported": imported, "failed": failed}


# ===================================================================
# READER & STREAMING ROUTES
# ===================================================================

@app.get("/reader/{book_id}", response_class=HTMLResponse)
def reader_page(
    request: Request,
    book_id: int,
    fmt: str | None = None,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    book = book_service.get_book_details(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    available_fmts = [f["format"].lower() for f in book["formats"]]
    chosen_fmt = None

    if fmt and fmt.lower() in available_fmts:
        chosen_fmt = fmt.lower()
    elif "epub" in available_fmts:
        chosen_fmt = "epub"
    elif "pdf" in available_fmts:
        chosen_fmt = "pdf"
    elif "fb2" in available_fmts or "fbz" in available_fmts:
        # Auto-convert FB2 to EPUB on the fly for in-browser reading
        if importer_service.ensure_book_epub_format(db, book_id):
            chosen_fmt = "epub"
            book = book_service.get_book_details(db, book_id)
        else:
            chosen_fmt = available_fmts[0] if available_fmts else None
    elif available_fmts:
        chosen_fmt = available_fmts[0]

    raw_url = f"/raw/{book_id}/book.{chosen_fmt}" if chosen_fmt else None

    return templates.TemplateResponse(
        request=request,
        name="reader.html",
        context={
            "book": book,
            "fmt": chosen_fmt,
            "raw_url": raw_url,
            "current_user": current_user,
        }
    )


@app.get("/raw/{book_id}/{filename}")
def raw_book_stream(
    book_id: int,
    filename: str,
    db: Session = Depends(get_db)
):
    # Extract format from filename (e.g. "book.epub" -> "EPUB", "epub" -> "EPUB")
    fmt_upper = filename.split(".")[-1].upper()
    row = db.execute(
        text("""
            SELECT b.path, d.name
            FROM books b
            JOIN data d ON b.id = d.book_id
            WHERE b.id = :id AND UPPER(d.format) = :fmt
            LIMIT 1
        """),
        {"id": book_id, "fmt": fmt_upper}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Book #{book_id} format {fmt_upper} not found")

    lib_root = Path(settings.LIBRARY_ROOT)
    file_path = lib_root / row.path / f"{row.name}.{fmt_upper.lower()}"
    if not file_path.is_file():
        alt_path = lib_root / row.path / f"{row.name}.{fmt_upper}"
        if alt_path.is_file():
            file_path = alt_path
        else:
            raise HTTPException(status_code=404, detail="File not found on disk")

    assert_safe_path(file_path, lib_root)

    media_type = MIME_TYPES.get(fmt_upper.lower(), "application/octet-stream")
    return FileResponse(
        str(file_path),
        media_type=media_type,
        headers={"Accept-Ranges": "bytes"}
    )


# ===================================================================
# COVERS & FILE DOWNLOADS (WITH RBAC ENFORCEMENT)
# ===================================================================

@app.get("/cover/{book_id}")
def get_cover(book_id: int, db: Session = Depends(get_db)):
    book = db.execute(
        text("SELECT path, has_cover FROM books WHERE id = :id"),
        {"id": book_id}
    ).fetchone()

    if not book or not book.has_cover or not book.path:
        return FileResponse(
            str(PLACEHOLDER_COVER),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=2592000"}
        )

    lib_root = Path(settings.LIBRARY_ROOT)
    cover_file = lib_root / book.path / "cover.jpg"
    assert_safe_path(cover_file, lib_root)
    if not cover_file.is_file():
        return FileResponse(
            str(PLACEHOLDER_COVER),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=2592000"}
        )

    return FileResponse(
        str(cover_file),
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=2592000, immutable"}
    )


@app.get("/thumbnail/{book_id}")
def get_thumbnail(book_id: int, db: Session = Depends(get_db)):
    try:
        thumb_dir = Path(settings.THUMB_CACHE_DIR)
        thumb_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        thumb_dir = BASE_DIR / "cache" / "thumbs"
        thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_file = thumb_dir / f"{book_id}.webp"

    if thumb_file.is_file():
        return FileResponse(
            str(thumb_file),
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=2592000, immutable"}
        )

    book = db.execute(
        text("SELECT path, has_cover FROM books WHERE id = :id"),
        {"id": book_id}
    ).fetchone()

    if not book or not book.has_cover or not book.path:
        return FileResponse(
            str(PLACEHOLDER_COVER),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=2592000"}
        )

    lib_root = Path(settings.LIBRARY_ROOT)
    cover_file = lib_root / book.path / "cover.jpg"
    assert_safe_path(cover_file, lib_root)
    if not cover_file.is_file():
        return FileResponse(
            str(PLACEHOLDER_COVER),
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=2592000"}
        )

    try:
        from PIL import Image
        with Image.open(cover_file) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((240, 360), Image.Resampling.LANCZOS)
            img.save(str(thumb_file), format="WEBP", quality=80, method=4)
        return FileResponse(
            str(thumb_file),
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=2592000, immutable"}
        )
    except Exception as e:
        logger.warning(f"Failed to generate thumbnail for book #{book_id}: {e}")
        return FileResponse(
            str(cover_file),
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=2592000, immutable"}
        )


@app.get("/download/{book_id}/{fmt}")
def download_book(
    request: Request,
    book_id: int,
    fmt: str,
    db: Session = Depends(get_db)
):
    # Role-Based Permission Check: Downloads require Power User or Admin role
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please sign in to download book files."
        )
    if not user["can_download"]:
        raise HTTPException(
            status_code=403,
            detail="Downloads are restricted to Power Users and Administrators. Standard Users have online reading access."
        )

    fmt_upper = fmt.upper()
    row = db.execute(
        text("""
            SELECT b.path, b.title, d.name
            FROM books b
            JOIN data d ON b.id = d.book_id
            WHERE b.id = :id AND UPPER(d.format) = :fmt
            LIMIT 1
        """),
        {"id": book_id, "fmt": fmt_upper}
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Book #{book_id} in format {fmt_upper} not found")

    lib_root = Path(settings.LIBRARY_ROOT)
    file_path = lib_root / row.path / f"{row.name}.{fmt.lower()}"
    if not file_path.is_file():
        alt_path = lib_root / row.path / f"{row.name}.{fmt_upper}"
        if alt_path.is_file():
            file_path = alt_path
        else:
            raise HTTPException(status_code=404, detail=f"File {file_path.name} not found on disk")

    assert_safe_path(file_path, lib_root)

    ascii_title = "".join(c for c in row.title if c.isalnum() and ord(c) < 128 or c in (" ", "-", "_")).strip()
    fallback_filename = f"{ascii_title}.{fmt.lower()}" if ascii_title else f"book_{book_id}.{fmt.lower()}"
    utf8_filename = f"{row.title}.{fmt.lower()}"
    encoded_filename = urllib.parse.quote(utf8_filename)
    media_type = MIME_TYPES.get(fmt.lower(), "application/octet-stream")

    return FileResponse(
        str(file_path),
        media_type=media_type,
        filename=fallback_filename,
        headers={
            "Content-Disposition": f"attachment; filename=\"{fallback_filename}\"; filename*=UTF-8''{encoded_filename}"
        }
    )


# ===================================================================
# REST API ROUTES
# ===================================================================

@app.get("/api/books")
def api_list_books(
    q: str | None = None,
    author_id: str | None = None,
    series_id: str | None = None,
    tag_id: str | None = None,
    format_name: str | None = None,
    sort_by: str = "recent",
    page: str | int = 1,
    size: str | int = 36,
    db: Session = Depends(get_db)
):
    aid = parse_int_param(author_id)
    sid = parse_int_param(series_id)
    tid = parse_int_param(tag_id)
    p = parse_int_param(page) or 1
    s = parse_int_param(size) or 36

    return book_service.get_books(
        db=db,
        page=p,
        size=s,
        q=q,
        author_id=aid,
        series_id=sid,
        tag_id=tid,
        format_name=format_name,
        sort_by=sort_by
    )


@app.get("/api/books/{book_id}")
def api_get_book(book_id: int, db: Session = Depends(get_db)):
    book = book_service.get_book_details(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@app.get("/api/stats")
def api_stats(db: Session = Depends(get_db)):
    return book_service.get_stats(db)


@app.get("/api/authors/suggest")
def api_suggest_authors(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, name
            FROM authors
            WHERE name ILIKE :q
            ORDER BY name
            LIMIT 10
        """),
        {"q": f"%{q}%"}
    ).fetchall()
    return [{"id": r.id, "name": r.name} for r in rows]


@app.get("/api/series/suggest")
def api_suggest_series(q: str = Query(..., min_length=2), db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, name
            FROM series
            WHERE name ILIKE :q
            ORDER BY name
            LIMIT 10
        """),
        {"q": f"%{q}%"}
    ).fetchall()
    return [{"id": r.id, "name": r.name} for r in rows]


# ===================================================================
# TEXT-TO-SPEECH (TTS) AUDIOBOOK ROUTES (KOKORO-82M NEURAL)
# ===================================================================

class SynthesizeRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0
    lang: str | None = None


@app.get("/api/tts/voices")
def get_tts_voices():
    """Return available high-quality neural voices and the default selection."""
    return {
        "voices": tts_service.VOICE_CATALOG,
        "default": tts_service.DEFAULT_VOICE
    }


@app.post("/api/tts/synthesize")
def synthesize_tts(req: SynthesizeRequest):
    """Synthesize text into speech and return MP3 audio URL and duration."""
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        res = tts_service.synthesize_speech(
            text=req.text,
            voice=req.voice,
            speed=req.speed,
            lang=req.lang
        )
        if "error" in res:
            raise HTTPException(status_code=400, detail=res["error"])
        return res
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"TTS synthesis error: {str(e)}")


@app.get("/api/tts/audio/{filename}")
@app.head("/api/tts/audio/{filename}")
def get_tts_audio(filename: str):
    """Serve cached TTS audio file with byte range support for smooth scrubbing."""
    safe_name = Path(filename).name
    if not safe_name.endswith(".mp3"):
        raise HTTPException(status_code=400, detail="Invalid audio format")
    audio_file = tts_service.CACHE_DIR / safe_name
    assert_safe_path(audio_file, tts_service.CACHE_DIR)
    if not audio_file.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(
        str(audio_file),
        media_type="audio/mpeg",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=31536000, immutable"}
    )


# ===================================================================
# PROGRESSIVE WEB APP (PWA) ROUTES
# ===================================================================

@app.get("/manifest.json")
def get_pwa_manifest():
    manifest_path = STATIC_DIR / "manifest.json"
    return FileResponse(str(manifest_path), media_type="application/manifest+json")


@app.get("/sw.js")
def get_service_worker():
    sw_path = STATIC_DIR / "sw.js"
    return FileResponse(str(sw_path), media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})


# ===================================================================
# READING PROGRESS & CLOUD SYNC ROUTES
# ===================================================================

class ReadingProgressRequest(BaseModel):
    cfi: str
    percentage: float = 0.0


@app.get("/api/progress/{book_id}")
def api_get_reading_progress(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    prog = book_service.get_reading_progress(db, user["id"], book_id)
    if not prog:
        return JSONResponse(status_code=404, content={"detail": "No progress saved"})
    return prog


@app.post("/api/progress/{book_id}")
def api_save_reading_progress(
    request: Request,
    book_id: int,
    req: ReadingProgressRequest,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    res = book_service.save_reading_progress(db, user["id"], book_id, req.cfi, req.percentage)
    return res


# ===================================================================
# READING SHELVES (WANT TO READ / READING / FINISHED)
# ===================================================================

class ReadingShelfRequest(BaseModel):
    status: str  # 'reading', 'plan_to_read', 'finished', or 'none'


@app.get("/api/shelves/{book_id}")
def api_get_shelf_status(
    request: Request,
    book_id: int,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    st = book_service.get_reading_status(db, user["id"], book_id)
    return {"status": st}


@app.post("/api/shelves/{book_id}")
def api_set_shelf_status(
    request: Request,
    book_id: int,
    req: ReadingShelfRequest,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    new_status = book_service.set_reading_status(db, user["id"], book_id, req.status)
    counts = book_service.get_user_shelf_counts(db, user["id"])
    return {"status": new_status, "counts": counts}


# ===================================================================
# SEND TO KINDLE / EMAIL
# ===================================================================

class SendEmailRequest(BaseModel):
    recipient_email: str


@app.post("/api/books/{book_id}/send-email")
def api_send_book_email(
    request: Request,
    book_id: int,
    req: SendEmailRequest,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user["can_download"]:
        raise HTTPException(status_code=403, detail="Sending books requires download permissions")

    email_target = req.recipient_email.strip()
    if not email_target or "@" not in email_target:
        raise HTTPException(status_code=400, detail="Invalid email address")

    if not settings.SMTP_HOST:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "SMTP server not configured. Please set SMTP_HOST in settings to enable Send to Kindle/Email."
            }
        )

    # Find best format: EPUB preferred, else MOBI, PDF
    formats = db.execute(
        text("SELECT format, name FROM data WHERE book_id = :id ORDER BY CASE WHEN format = 'EPUB' THEN 1 WHEN format = 'MOBI' THEN 2 ELSE 3 END"),
        {"id": book_id}
    ).fetchall()
    if not formats:
        raise HTTPException(status_code=404, detail="No readable book formats found")

    fmt_row = formats[0]
    fmt = fmt_row.format
    b_row = db.execute(text("SELECT title, path FROM books WHERE id = :id"), {"id": book_id}).fetchone()
    if not b_row or not b_row.path:
        raise HTTPException(status_code=404, detail="Book path not found")

    lib_root = Path(settings.LIBRARY_ROOT)
    file_path = lib_root / b_row.path / f"{fmt_row.name}.{fmt.lower()}"
    if not file_path.is_file():
        file_path = lib_root / b_row.path / f"{fmt_row.name}.{fmt.upper()}"
        if not file_path.is_file():
            raise HTTPException(status_code=404, detail="Book file not found on disk")

    assert_safe_path(file_path, lib_root)

    # Check file size (email servers typically cap at 25MB - 50MB)
    if file_path.stat().st_size > 45 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Book file exceeds 45MB email attachment limit")

    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = f"Book: {b_row.title}"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = email_target
    msg.set_content(f"Here is '{b_row.title}' from your eBook Library.\n\nEnjoy reading!")

    with open(file_path, "rb") as f:
        file_data = f.read()
    msg.add_attachment(
        file_data,
        maintype="application",
        subtype=fmt.lower(),
        filename=f"{b_row.title}.{fmt.lower()}"
    )

    try:
        use_ssl = getattr(settings, "SMTP_SSL", False) or settings.SMTP_PORT == 465
        if use_ssl:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=25) as server:
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        elif settings.SMTP_TLS:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=25) as server:
                server.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=25) as server:
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(msg)
        return {"success": True, "message": f"Successfully sent '{b_row.title}' ({fmt}) to {email_target}!"}
    except Exception as e:
        logger.error(f"Failed to send book email: {e}")
        return JSONResponse(status_code=500, content={"success": False, "message": f"SMTP delivery error: {str(e)}"})


# ===================================================================
# OPDS 1.2 CATALOG FEED ROUTES (FOR MOON+, APPLE BOOKS, KOReader, etc.)
# ===================================================================

@app.get("/opds")
def opds_root(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    stats = book_service.get_stats(db)
    xml_content = opds_service.generate_root_catalog(base_url=base_url, stats=stats)
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_NAVIGATION)


@app.get("/opds/opensearch.xml")
def opds_opensearch(request: Request):
    base_url = str(request.base_url).rstrip("/")
    xml_content = opds_service.generate_opensearch_xml(base_url=base_url)
    return Response(content=xml_content, media_type=opds_service.OPENSEARCH_MIME)


@app.get("/opds/recent")
def opds_recent(
    request: Request,
    page: int = 1,
    size: int = 30,
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    res = book_service.get_books(db, page=page, size=size, sort_by="recent")
    self_url = f"{base_url}/opds/recent?page={res['page']}"
    next_url = f"{base_url}/opds/recent?page={res['page']+1}" if res['page'] < res['total_pages'] else None
    prev_url = f"{base_url}/opds/recent?page={res['page']-1}" if res['page'] > 1 else None
    xml_content = opds_service.generate_acquisition_feed(
        title="Recently Added Books",
        feed_id="urn:opds:recent",
        self_url=self_url,
        books=res["books"],
        page=res["page"],
        total_pages=res["total_pages"],
        base_url=base_url,
        next_url=next_url,
        prev_url=prev_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_ACQUISITION)


@app.get("/opds/search")
def opds_search(
    request: Request,
    q: str = "",
    page: int = 1,
    size: int = 30,
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    res = book_service.get_books(db, page=page, size=size, q=q, search_descriptions=True)
    q_enc = urllib.parse.quote(q)
    self_url = f"{base_url}/opds/search?q={q_enc}&page={res['page']}"
    next_url = f"{base_url}/opds/search?q={q_enc}&page={res['page']+1}" if res['page'] < res['total_pages'] else None
    prev_url = f"{base_url}/opds/search?q={q_enc}&page={res['page']-1}" if res['page'] > 1 else None
    xml_content = opds_service.generate_acquisition_feed(
        title=f"Search: {q}",
        feed_id=f"urn:opds:search:{q_enc}",
        self_url=self_url,
        books=res["books"],
        page=res["page"],
        total_pages=res["total_pages"],
        base_url=base_url,
        next_url=next_url,
        prev_url=prev_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_ACQUISITION)


@app.get("/opds/authors")
def opds_authors(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    rows = db.execute(text("""
        SELECT a.id, a.name, count(*) AS count
        FROM authors a
        JOIN book_authors ba ON a.id = ba.author_id
        GROUP BY a.id, a.name
        ORDER BY a.name
        LIMIT 100
    """)).fetchall()
    items = [{"id": r.id, "name": r.name, "count": r.count, "href": f"{base_url}/opds/authors/{r.id}"} for r in rows]
    xml_content = opds_service.generate_navigation_list_feed(
        title="Authors",
        feed_id="urn:opds:authors",
        self_url=f"{base_url}/opds/authors",
        items=items,
        base_url=base_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_NAVIGATION)


@app.get("/opds/authors/{author_id}")
def opds_author_books(
    request: Request,
    author_id: int,
    page: int = 1,
    size: int = 30,
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    author_name = db.execute(text("SELECT name FROM authors WHERE id = :id"), {"id": author_id}).scalar() or f"Author #{author_id}"
    res = book_service.get_books(db, page=page, size=size, author_id=author_id)
    self_url = f"{base_url}/opds/authors/{author_id}?page={res['page']}"
    next_url = f"{base_url}/opds/authors/{author_id}?page={res['page']+1}" if res['page'] < res['total_pages'] else None
    prev_url = f"{base_url}/opds/authors/{author_id}?page={res['page']-1}" if res['page'] > 1 else None
    xml_content = opds_service.generate_acquisition_feed(
        title=f"Books by {author_name}",
        feed_id=f"urn:opds:authors:{author_id}",
        self_url=self_url,
        books=res["books"],
        page=res["page"],
        total_pages=res["total_pages"],
        base_url=base_url,
        next_url=next_url,
        prev_url=prev_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_ACQUISITION)


@app.get("/opds/series")
def opds_series(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    rows = db.execute(text("""
        SELECT s.id, s.name, count(*) AS count
        FROM series s
        JOIN book_series bs ON s.id = bs.series_id
        GROUP BY s.id, s.name
        ORDER BY count DESC
        LIMIT 100
    """)).fetchall()
    items = [{"id": r.id, "name": r.name, "count": r.count, "href": f"{base_url}/opds/series/{r.id}"} for r in rows]
    xml_content = opds_service.generate_navigation_list_feed(
        title="Series",
        feed_id="urn:opds:series",
        self_url=f"{base_url}/opds/series",
        items=items,
        base_url=base_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_NAVIGATION)


@app.get("/opds/series/{series_id}")
def opds_series_books(
    request: Request,
    series_id: int,
    page: int = 1,
    size: int = 30,
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    series_name = db.execute(text("SELECT name FROM series WHERE id = :id"), {"id": series_id}).scalar() or f"Series #{series_id}"
    res = book_service.get_books(db, page=page, size=size, series_id=series_id)
    self_url = f"{base_url}/opds/series/{series_id}?page={res['page']}"
    next_url = f"{base_url}/opds/series/{series_id}?page={res['page']+1}" if res['page'] < res['total_pages'] else None
    prev_url = f"{base_url}/opds/series/{series_id}?page={res['page']-1}" if res['page'] > 1 else None
    xml_content = opds_service.generate_acquisition_feed(
        title=f"Series: {series_name}",
        feed_id=f"urn:opds:series:{series_id}",
        self_url=self_url,
        books=res["books"],
        page=res["page"],
        total_pages=res["total_pages"],
        base_url=base_url,
        next_url=next_url,
        prev_url=prev_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_ACQUISITION)


@app.get("/opds/genres")
def opds_genres(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    rows = db.execute(text("""
        SELECT t.id, t.name, count(*) AS count
        FROM tags t
        JOIN book_tags bt ON t.id = bt.tag_id
        GROUP BY t.id, t.name
        ORDER BY count DESC
        LIMIT 100
    """)).fetchall()
    items = [{"id": r.id, "name": r.name, "count": r.count, "href": f"{base_url}/opds/genres/{r.id}"} for r in rows]
    xml_content = opds_service.generate_navigation_list_feed(
        title="Genres & Tags",
        feed_id="urn:opds:genres",
        self_url=f"{base_url}/opds/genres",
        items=items,
        base_url=base_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_NAVIGATION)


@app.get("/opds/genres/{tag_id}")
def opds_genre_books(
    request: Request,
    tag_id: int,
    page: int = 1,
    size: int = 30,
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    tag_name = db.execute(text("SELECT name FROM tags WHERE id = :id"), {"id": tag_id}).scalar() or f"Genre #{tag_id}"
    res = book_service.get_books(db, page=page, size=size, tag_id=tag_id)
    self_url = f"{base_url}/opds/genres/{tag_id}?page={res['page']}"
    next_url = f"{base_url}/opds/genres/{tag_id}?page={res['page']+1}" if res['page'] < res['total_pages'] else None
    prev_url = f"{base_url}/opds/genres/{tag_id}?page={res['page']-1}" if res['page'] > 1 else None
    xml_content = opds_service.generate_acquisition_feed(
        title=f"Genre: {tag_name}",
        feed_id=f"urn:opds:genres:{tag_id}",
        self_url=self_url,
        books=res["books"],
        page=res["page"],
        total_pages=res["total_pages"],
        base_url=base_url,
        next_url=next_url,
        prev_url=prev_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_ACQUISITION)


@app.get("/opds/languages")
def opds_languages(request: Request, db: Session = Depends(get_db)):
    base_url = str(request.base_url).rstrip("/")
    rows = db.execute(text("""
        SELECT l.id, l.lang_code, count(*) AS count
        FROM languages l
        JOIN book_languages bl ON l.id = bl.language_id
        GROUP BY l.id, l.lang_code
        ORDER BY count DESC
        LIMIT 50
    """)).fetchall()
    items = [
        {
            "id": r.id,
            "name": f"{book_service.format_language_name(r.lang_code)} ({r.lang_code.upper() if r.lang_code else ''})",
            "count": r.count,
            "href": f"{base_url}/opds/languages/{r.id}"
        }
        for r in rows
    ]
    xml_content = opds_service.generate_navigation_list_feed(
        title="Languages",
        feed_id="urn:opds:languages",
        self_url=f"{base_url}/opds/languages",
        items=items,
        base_url=base_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_NAVIGATION)


@app.get("/opds/languages/{lang_id}")
def opds_language_books(
    request: Request,
    lang_id: int,
    page: int = 1,
    size: int = 30,
    db: Session = Depends(get_db)
):
    base_url = str(request.base_url).rstrip("/")
    lang_code = db.execute(text("SELECT lang_code FROM languages WHERE id = :id"), {"id": lang_id}).scalar()
    lang_name = book_service.format_language_name(lang_code) if lang_code else f"Language #{lang_id}"
    res = book_service.get_books(db, page=page, size=size, language_id=lang_id)
    self_url = f"{base_url}/opds/languages/{lang_id}?page={res['page']}"
    next_url = f"{base_url}/opds/languages/{lang_id}?page={res['page']+1}" if res['page'] < res['total_pages'] else None
    prev_url = f"{base_url}/opds/languages/{lang_id}?page={res['page']-1}" if res['page'] > 1 else None
    xml_content = opds_service.generate_acquisition_feed(
        title=f"Language: {lang_name}",
        feed_id=f"urn:opds:languages:{lang_id}",
        self_url=self_url,
        books=res["books"],
        page=res["page"],
        total_pages=res["total_pages"],
        base_url=base_url,
        next_url=next_url,
        prev_url=prev_url
    )
    return Response(content=xml_content, media_type=opds_service.OPDS_MIME_ACQUISITION)


# ===================================================================
# READER NOTES & BOOKMARKS API
# ===================================================================

class CreateNoteRequest(BaseModel):
    cfi: str
    text: str
    note: str = ""
    color: str = "yellow"


class CreateBookmarkRequest(BaseModel):
    cfi: str
    label: str = ""
    percentage: int = 0


class AutoSyncScheduleRequest(BaseModel):
    enabled: bool
    hour: int = 2
    minute: int = 0


@app.get("/api/books/{book_id}/notes")
def api_get_book_notes(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        return []
    return book_service.get_user_notes(db, current_user["id"], book_id)


@app.post("/api/books/{book_id}/notes")
def api_add_book_note(
    book_id: int,
    req: CreateNoteRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return book_service.add_user_note(
        db,
        user_id=current_user["id"],
        book_id=book_id,
        cfi=req.cfi,
        text_content=req.text,
        note=req.note,
        color=req.color
    )


@app.delete("/api/notes/{note_id}")
def api_delete_note(
    note_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    success = book_service.delete_user_note(db, current_user["id"], note_id)
    return {"success": success}


@app.get("/api/books/{book_id}/bookmarks")
def api_get_book_bookmarks(
    book_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        return []
    return book_service.get_user_bookmarks(db, current_user["id"], book_id)


@app.post("/api/books/{book_id}/bookmarks")
def api_add_book_bookmark(
    book_id: int,
    req: CreateBookmarkRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return book_service.add_user_bookmark(
        db,
        user_id=current_user["id"],
        book_id=book_id,
        cfi=req.cfi,
        label=req.label,
        percentage=req.percentage
    )


@app.delete("/api/bookmarks/{bookmark_id}")
def api_delete_bookmark(
    bookmark_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    current_user = get_current_user(request, db)
    if not current_user:
        raise HTTPException(status_code=401, detail="Authentication required")
    success = book_service.delete_user_bookmark(db, current_user["id"], bookmark_id)
    return {"success": success}


# ===================================================================
# USER PROFILE READING STATS MODAL
# ===================================================================

@app.get("/profile/stats-modal", response_class=HTMLResponse)
def reading_stats_modal(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    stats = book_service.get_user_reading_stats(db, user["id"])
    return templates.TemplateResponse(
        request=request,
        name="partials/reading_stats_modal.html",
        context={
            "current_user": user,
            "stats": stats
        }
    )


# ===================================================================
# ADMIN DUPLICATES & AUTO-SYNC ROUTES
# ===================================================================

@app.get("/admin/maintenance/duplicates")
def admin_get_duplicate_books(
    limit: int = 50,
    admin: dict = Depends(require_admin),
    db: Session = Depends(get_db)
):
    duplicates = admin_settings_service.find_duplicate_books(db, limit=limit)
    return {"success": True, "duplicates": duplicates}


@app.get("/admin/settings/auto-sync")
def admin_get_auto_sync_status(
    admin: dict = Depends(require_admin)
):
    return admin_settings_service.get_auto_sync_status()


@app.post("/admin/settings/auto-sync")
def admin_set_auto_sync_schedule(
    req: AutoSyncScheduleRequest,
    admin: dict = Depends(require_admin)
):
    return admin_settings_service.set_auto_sync_schedule(
        enabled=req.enabled,
        hour=req.hour,
        minute=req.minute
    )


# ===================================================================
# COVER REPLACEMENT & OPENLIBRARY FETCHER
# ===================================================================

@app.post("/admin/book/{book_id}/upload-cover")
async def admin_upload_book_cover(
    book_id: int,
    cover_file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.get("is_power_user"):
        raise HTTPException(status_code=403, detail="Permission denied")

    content = await cover_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty cover file uploaded")

    res = admin_settings_service.replace_book_cover(db, book_id, content)
    return res


@app.post("/admin/book/{book_id}/fetch-cover")
def admin_fetch_openlibrary_cover(
    book_id: int,
    request: Request = None,
    db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user or not user.get("is_power_user"):
        raise HTTPException(status_code=403, detail="Permission denied")

    res = admin_settings_service.fetch_cover_from_openlibrary(db, book_id)
    return res


