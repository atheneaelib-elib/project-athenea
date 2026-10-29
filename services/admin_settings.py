import os
import shutil
import smtplib
import logging
import subprocess
import re
import threading
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import text

try:
    from ..settings import settings
except ImportError:
    from settings import settings

try:
    from . import books as book_service
except ImportError:
    import books as book_service

logger = logging.getLogger("bookserver.admin_settings")

PRIMARY_ENV_PATH = Path("/etc/athenea.env")
LEGACY_ENV_PATH = Path("/etc/bookserver.env")
FALLBACK_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_PROD_BACKUP = Path("/mnt/mainlib/Backups/db")
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/mnt/mainlib/Backups/db" if _PROD_BACKUP.exists() else str(Path(__file__).resolve().parent.parent / "backups")))
BACKUP_SCRIPT = Path("/opt/athenea/backup_db.sh") if Path("/opt/athenea/backup_db.sh").exists() else Path("/opt/bookserver/backup_db.sh")


def get_target_env_path() -> Path:
    """Return the active configuration file path."""
    if PRIMARY_ENV_PATH.exists():
        return PRIMARY_ENV_PATH
    if FALLBACK_ENV_PATH.exists():
        return FALLBACK_ENV_PATH
    if LEGACY_ENV_PATH.exists():
        return LEGACY_ENV_PATH
    if os.access(PRIMARY_ENV_PATH.parent, os.W_OK):
        return PRIMARY_ENV_PATH
    return FALLBACK_ENV_PATH


def read_raw_env_file() -> tuple[Path, list[str]]:
    """Read the current environment file lines."""
    env_path = get_target_env_path()
    lines = []
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.error(f"Error reading env file {env_path}: {e}")
    return env_path, lines


def _update_single_env_file(env_path: Path, key_values: dict[str, str]):
    lines = []
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logger.error(f"Error reading env file {env_path}: {e}")

    updated_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            k, _ = line.split("=", 1)
            k = k.strip()
            if k in key_values:
                val = key_values[k]
                new_lines.append(f"{k}={val}\n")
                updated_keys.add(k)
                continue
        new_lines.append(line)

    remaining = [k for k in key_values if k not in updated_keys]
    if remaining:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        for k in remaining:
            new_lines.append(f"{k}={key_values[k]}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def update_env_file(key_values: dict[str, str]) -> Path:
    """
    Update or append key=value pairs in the configuration file,
    preserving comments and existing formatting. Also syncs secondary env files if present.
    """
    env_path = get_target_env_path()
    try:
        _update_single_env_file(env_path, key_values)
    except Exception as e:
        logger.error(f"Failed to write configuration file {env_path}: {e}")
        raise RuntimeError(f"Unable to write to {env_path}: {e}")

    # Keep any secondary existing env files synchronized so restarts always pick up latest values
    for extra_path in (PRIMARY_ENV_PATH, FALLBACK_ENV_PATH, LEGACY_ENV_PATH):
        if extra_path != env_path and extra_path.exists():
            try:
                _update_single_env_file(extra_path, key_values)
            except Exception as ex:
                logger.warning(f"Could not sync secondary env file {extra_path}: {ex}")

    for k, v in key_values.items():
        os.environ[k] = str(v)

    return env_path


def test_database_connection(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str
) -> dict:
    """Test a PostgreSQL connection with the supplied parameters and return diagnostic info."""
    import psycopg2

    clean_host = (host or "").strip() or "localhost"
    clean_dbname = (dbname or "").strip() or "athenea"
    clean_user = (user or "").strip() or "athenea"
    clean_port = int(port) if str(port).isdigit() else 5432

    if not password or password in ("••••••••", "********"):
        effective_password = settings.DB_PASSWORD
    else:
        effective_password = password

    if not effective_password:
        return {
            "success": False,
            "message": "DB_PASSWORD is required. Please enter the PostgreSQL password."
        }

    try:
        conn = psycopg2.connect(
            host=clean_host,
            port=clean_port,
            dbname=clean_dbname,
            user=clean_user,
            password=effective_password,
            connect_timeout=5,
            client_encoding="utf8"
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                ver_row = cur.fetchone()
                pg_ver = (ver_row[0].split(",")[0] if ver_row and ver_row[0] else "PostgreSQL")

                cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
                sz_row = cur.fetchone()
                db_size = sz_row[0] if sz_row and sz_row[0] else "Unknown"

                cur.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'books'
                    );
                """)
                has_books_table = bool(cur.fetchone()[0])
                book_count = None
                if has_books_table:
                    cur.execute("SELECT count(*) FROM books;")
                    book_count = cur.fetchone()[0]
        finally:
            conn.close()

        books_msg = f", {book_count:,} books in catalog" if book_count is not None else " (empty/new schema)"
        return {
            "success": True,
            "pg_version": pg_ver,
            "db_size": db_size,
            "book_count": book_count,
            "message": f"Connected to {clean_user}@{clean_host}:{clean_port}/{clean_dbname} ({pg_ver}, Size: {db_size}{books_msg})."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"PostgreSQL connection failed: {str(e).strip()}"
        }


def save_database_settings(
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str
) -> dict:
    """
    Validate, persist, and hot-reload PostgreSQL connection parameters
    (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).
    """
    clean_host = (host or "").strip() or "localhost"
    clean_dbname = (dbname or "").strip() or "athenea"
    clean_user = (user or "").strip() or "athenea"
    clean_port = int(port) if str(port).isdigit() else 5432

    if not password or password in ("••••••••", "********"):
        effective_password = settings.DB_PASSWORD
    else:
        effective_password = password

    # Verify the connection succeeds before applying so we don't lock out the server
    test_res = test_database_connection(
        host=clean_host,
        port=clean_port,
        dbname=clean_dbname,
        user=clean_user,
        password=effective_password
    )
    if not test_res.get("success"):
        return test_res

    updates = {
        "DB_HOST": clean_host,
        "DB_PORT": str(clean_port),
        "DB_NAME": clean_dbname,
        "DB_USER": clean_user,
        "DB_PASSWORD": effective_password,
    }

    env_path = update_env_file(updates)

    settings.DB_HOST = clean_host
    settings.DB_PORT = clean_port
    settings.DB_NAME = clean_dbname
    settings.DB_USER = clean_user
    settings.DB_PASSWORD = effective_password

    # Reinitialize live SQLAlchemy engine & SessionLocal pool
    try:
        try:
            from .. import db as db_module
            from . import auth as auth_service
        except ImportError:
            import db as db_module
            from services import auth as auth_service

        db_module.reinit_engine(
            db_user=clean_user,
            db_password=effective_password,
            db_host=clean_host,
            db_port=clean_port,
            db_name=clean_dbname,
        )
        # Ensure auth/permission schema exists on the target database
        with db_module.SessionLocal() as session:
            auth_service.init_auth_tables(session)
    except Exception as e:
        logger.error(f"Error reinitializing SQLAlchemy engine: {e}")
        return {
            "success": False,
            "message": f"Saved to {env_path.name}, but live engine reload failed: {e}"
        }

    try:
        book_service.invalidate_caches()
    except Exception:
        pass

    logger.info(
        f"Updated PostgreSQL settings in {env_path}: "
        f"{clean_user}@{clean_host}:{clean_port}/{clean_dbname}"
    )
    return {
        "success": True,
        "message": f"PostgreSQL settings saved to {env_path.name} and live connection pool reinitialized! ({test_res.get('message')})",
        "db_size": test_res.get("db_size"),
        "book_count": test_res.get("book_count"),
    }


def save_registration_settings(allow_registration: bool, require_approval: bool) -> dict:
    """Save user registration settings to disk and live memory."""
    updates = {
        "ALLOW_REGISTRATION": "true" if allow_registration else "false",
        "REQUIRE_ADMIN_APPROVAL": "true" if require_approval else "false",
    }
    update_env_file(updates)
    settings.ALLOW_REGISTRATION = allow_registration
    settings.REQUIRE_ADMIN_APPROVAL = require_approval
    logger.info(f"Updated registration settings: allow={allow_registration}, require_approval={require_approval}")
    return {"success": True, "message": "User registration settings updated successfully."}


def verify_books_path(path_str: str) -> dict:
    """Validate a candidate books library root directory path."""
    path_str = (path_str or "").strip()
    if not path_str:
        return {"success": False, "message": "Books library path cannot be empty."}

    p = Path(path_str)
    if not p.exists():
        return {
            "success": False,
            "exists": False,
            "message": f"Directory '{path_str}' does not exist on the server."
        }

    if not p.is_dir():
        return {
            "success": False,
            "exists": True,
            "is_dir": False,
            "message": f"Path '{path_str}' is a regular file, not a directory."
        }

    # Test read/write access
    is_readable = os.access(p, os.R_OK)
    is_writable = os.access(p, os.W_OK)

    # Disk usage
    disk_info = {"total": "Unknown", "used": "Unknown", "free": "Unknown", "percent": 0}
    try:
        usage = shutil.disk_usage(p)
        disk_info = {
            "total": f"{usage.total / (1024**4):.1f} TB" if usage.total > 1024**4 else f"{usage.total / (1024**3):.1f} GB",
            "used": f"{usage.used / (1024**4):.1f} TB" if usage.used > 1024**4 else f"{usage.used / (1024**3):.1f} GB",
            "free": f"{usage.free / (1024**4):.1f} TB" if usage.free > 1024**4 else f"{usage.free / (1024**3):.1f} GB",
            "percent": round((usage.used / usage.total) * 100, 1) if usage.total else 0,
        }
    except Exception as e:
        logger.warning(f"Could not read disk usage for {p}: {e}")

    entry_count = 0
    try:
        entry_count = sum(1 for _ in p.iterdir())
    except Exception:
        pass

    return {
        "success": True,
        "exists": True,
        "is_dir": True,
        "readable": is_readable,
        "writable": is_writable,
        "top_level_entries": entry_count,
        "disk_info": disk_info,
        "message": f"Valid directory confirmed: {disk_info['free']} free space, {entry_count:,} top-level entries.",
    }


def save_library_path(path_str: str) -> dict:
    """Save updated books library root path to /etc/bookserver.env and active runtime."""
    verify_res = verify_books_path(path_str)
    if not verify_res.get("success"):
        return verify_res

    clean_path = str(Path(path_str.strip()).resolve())

    env_path = update_env_file({"LIBRARY_ROOT": clean_path})
    settings.LIBRARY_ROOT = clean_path

    try:
        book_service.invalidate_caches()
    except Exception:
        pass

    logger.info(f"Updated books library root path in {env_path}: LIBRARY_ROOT={clean_path}")
    return {
        "success": True,
        "message": f"Books path successfully updated to {clean_path} and saved to {env_path.name}.",
        "path": clean_path,
        "disk_info": verify_res.get("disk_info"),
    }



def save_smtp_settings(
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    tls: bool,
    ssl: bool = False
) -> dict:
    """Save SMTP configuration to both disk and live in-memory settings."""
    host = (host or "").strip()
    from_addr = (from_addr or "").strip() or getattr(settings, "SMTP_FROM", "books@example.com")
    user = (user or "").strip()
    if not user and from_addr and from_addr != "books@example.com" and "@" in from_addr:
        user = from_addr
    port = int(port) if str(port).isdigit() else 587

    if not password or password in ("••••••••", "********"):
        effective_password = settings.SMTP_PASSWORD
    else:
        effective_password = password.strip()
        if "gmail.com" in host.lower():
            effective_password = effective_password.replace(" ", "")

    updates = {
        "SMTP_HOST": host,
        "SMTP_PORT": str(port),
        "SMTP_USER": user,
        "SMTP_PASSWORD": effective_password,
        "SMTP_FROM": from_addr,
        "SMTP_TLS": "true" if tls else "false",
        "SMTP_SSL": "true" if ssl else "false",
    }

    env_path = update_env_file(updates)

    settings.SMTP_HOST = host
    settings.SMTP_PORT = port
    settings.SMTP_USER = user
    settings.SMTP_PASSWORD = effective_password
    settings.SMTP_FROM = from_addr
    settings.SMTP_TLS = tls
    settings.SMTP_SSL = ssl

    logger.info(f"Updated SMTP settings in {env_path}: host={host}, port={port}, from={from_addr}")
    return {"success": True, "message": "SMTP settings saved successfully."}


def test_smtp_connection(
    host: str,
    port: int,
    user: str,
    password: str,
    from_addr: str,
    tls: bool,
    ssl: bool = False,
    test_recipient: str = ""
) -> dict:
    """Test SMTP handshake, authentication, and optionally send a test email."""
    host = (host or "").strip()
    if not host:
        return {"success": False, "message": "SMTP Host is required."}

    port = int(port) if str(port).isdigit() else 587
    from_addr = (from_addr or "").strip() or getattr(settings, "SMTP_FROM", "books@example.com")
    user = (user or "").strip() or getattr(settings, "SMTP_USER", "")
    if not user and from_addr and from_addr != "books@example.com" and "@" in from_addr:
        user = from_addr

    if not password or password in ("••••••••", "********"):
        password = settings.SMTP_PASSWORD
    else:
        password = password.strip()
        if "gmail.com" in host.lower():
            password = password.replace(" ", "")

    if host.lower() not in ("localhost", "127.0.0.1") and not password:
        return {
            "success": False,
            "message": (
                f"SMTP Password is required to authenticate with {host}. "
                + ("For Gmail (smtp.gmail.com), enter your 16-character Google App Password in the SMTP Password field." if "gmail.com" in host.lower() else "Please enter your SMTP Password.")
            )
        }

    # Automatically enforce proper SSL/TLS mode according to port
    if port == 465:
        use_ssl = True
        use_tls = False
    elif port == 587:
        use_ssl = False
        use_tls = True
    else:
        use_ssl = bool(ssl)
        use_tls = bool(tls and not use_ssl)

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)

        with server:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()

            if user and password:
                try:
                    server.login(user, password)
                except smtplib.SMTPAuthenticationError as auth_err:
                    err_detail = auth_err.smtp_error.decode(errors="replace") if isinstance(auth_err.smtp_error, bytes) else str(auth_err.smtp_error)
                    msg = f"Authentication rejected ({auth_err.smtp_code} {err_detail})."
                    if "gmail.com" in host.lower():
                        msg += (
                            f" For Google Mail, please verify that: 1) You are using a 16-character App Password (not your normal Google account password), "
                            f"2) 2-Step Verification is enabled on the account '{user}', and 3) The App Password was created specifically under '{user}' "
                            f"(https://myaccount.google.com/apppasswords)."
                        )
                    return {"success": False, "message": msg}
                except smtplib.SMTPServerDisconnected as disc_err:
                    if "gmail.com" in host.lower():
                        msg = (
                            f"Google SMTP server rejected login credentials (BadCredentials): Connection closed by server. "
                            f"Google terminates the connection immediately when credentials are not recognized. "
                            f"Please verify: 1) You are using a 16-character App Password without typos, "
                            f"2) The App Password was generated while logged in as '{user}' at https://myaccount.google.com/apppasswords, and "
                            f"3) 2-Step Verification is active on '{user}'."
                        )
                    else:
                        msg = f"SMTP server disconnected during authentication ({disc_err})."
                    return {"success": False, "message": msg}

            if test_recipient and test_recipient.strip():
                recipient = test_recipient.strip()
                msg = EmailMessage()
                msg["Subject"] = "Athenea - SMTP Test Email"
                msg["From"] = from_addr
                msg["To"] = recipient
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                msg.set_content(
                    f"Hello,\n\n"
                    f"This is a confirmation test email from your Athenea Digital Library.\n"
                    f"Timestamp: {timestamp}\n"
                    f"SMTP Host: {host}:{port}\n"
                    f"Sender: {from_addr}\n\n"
                    f"If you received this message, 'Send to Kindle' and email delivery are working perfectly!\n"
                )
                server.send_message(msg)
                return {
                    "success": True,
                    "message": f"Success! Handshake, authentication ({user}), and test email sent to {recipient}."
                }

        return {
            "success": True,
            "message": f"Success! Connected to {host}:{port} and authenticated as {user}."
        }
    except Exception as e:
        logger.error(f"SMTP connection test failed: {e}")
        return {
            "success": False,
            "message": f"SMTP Error: {str(e)}"
        }


def get_fail2ban_status() -> dict:
    """Fetch live fail2ban status for bookserver jail via sudo."""
    status_data = {
        "active": False,
        "failed_current": 0,
        "failed_total": 0,
        "banned_current": 0,
        "banned_total": 0,
        "banned_ips": []
    }
    try:
        res = subprocess.run(
            ["sudo", "-n", "fail2ban-client", "status", "bookserver"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            status_data["active"] = True
            for line in res.stdout.splitlines():
                line = line.strip()
                if "Currently failed:" in line:
                    status_data["failed_current"] = int(line.split(":")[-1].strip() or 0)
                elif "Total failed:" in line:
                    status_data["failed_total"] = int(line.split(":")[-1].strip() or 0)
                elif "Currently banned:" in line:
                    status_data["banned_current"] = int(line.split(":")[-1].strip() or 0)
                elif "Total banned:" in line:
                    status_data["banned_total"] = int(line.split(":")[-1].strip() or 0)
                elif "Banned IP list:" in line:
                    raw_ips = line.split(":")[-1].strip()
                    if raw_ips:
                        status_data["banned_ips"] = [ip.strip() for ip in raw_ips.split() if ip.strip()]
    except Exception as e:
        logger.warning(f"Error reading fail2ban status: {e}")
    return status_data


def unban_ip(ip: str) -> dict:
    """Unban an IP address in fail2ban."""
    ip = ip.strip()
    # Basic IP validation
    if not re.match(r"^(\d{1,3}\.){3}\d{1,3}$", ip):
        return {"success": False, "message": "Invalid IPv4 address format."}

    try:
        res = subprocess.run(
            ["sudo", "-n", "fail2ban-client", "set", "bookserver", "unbanip", ip],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0:
            return {"success": True, "message": f"IP {ip} has been unbanned successfully."}
        else:
            return {"success": False, "message": f"Failed to unban IP: {res.stderr.strip()}"}
    except Exception as e:
        logger.error(f"Error unbanning IP {ip}: {e}")
        return {"success": False, "message": str(e)}


def get_thumbnail_cache_status() -> dict:
    """Return count and disk usage of cached WebP thumbnails."""
    cache_dir = Path(settings.THUMB_CACHE_DIR)
    count = 0
    total_bytes = 0
    if cache_dir.is_dir():
        try:
            for p in cache_dir.glob("*.webp"):
                count += 1
                total_bytes += p.stat().st_size
        except Exception as e:
            logger.warning(f"Error scanning thumbnail cache: {e}")
    return {
        "count": count,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
        "path": str(cache_dir)
    }


def clear_thumbnail_cache() -> dict:
    """Clear all cached WebP thumbnails from disk."""
    cache_dir = Path(settings.THUMB_CACHE_DIR)
    deleted = 0
    freed_bytes = 0
    if cache_dir.is_dir():
        try:
            for p in cache_dir.glob("*.webp"):
                freed_bytes += p.stat().st_size
                p.unlink(missing_ok=True)
                deleted += 1
        except Exception as e:
            logger.error(f"Error clearing thumbnail cache: {e}")
            return {"success": False, "message": f"Error clearing cache: {str(e)}"}
    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    return {
        "success": True,
        "message": f"Cleared {deleted} thumbnail(s), freeing {freed_mb} MB of disk space.",
        "deleted": deleted,
        "freed_mb": freed_mb
    }


def flush_query_cache() -> dict:
    """Invalidate in-memory sidebar and statistics query caches."""
    try:
        book_service.invalidate_caches()
        return {"success": True, "message": "In-memory sidebar and catalog caches invalidated successfully."}
    except Exception as e:
        logger.error(f"Error flushing query cache: {e}")
        return {"success": False, "message": str(e)}


def trigger_manual_backup() -> dict:
    """Trigger the automated PostgreSQL backup script via sudo."""
    if not BACKUP_SCRIPT.is_file():
        return {"success": False, "message": f"Backup script {BACKUP_SCRIPT} not found."}

    try:
        res = subprocess.run(
            ["sudo", "-n", str(BACKUP_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=120
        )
        if res.returncode == 0:
            return {"success": True, "message": "Database backup completed successfully."}
        else:
            return {"success": False, "message": f"Backup script error: {res.stderr.strip()}"}
    except Exception as e:
        logger.error(f"Error running manual backup: {e}")
        return {"success": False, "message": str(e)}


def vacuum_database(db: Session) -> dict:
    """Execute VACUUM ANALYZE to reclaim disk space and update query planner stats."""
    try:
        # VACUUM cannot run inside an open transaction block in Postgres
        with db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM ANALYZE;"))
        return {"success": True, "message": "PostgreSQL VACUUM ANALYZE completed successfully."}
    except Exception as e:
        logger.error(f"Error vacuuming database: {e}")
        return {"success": False, "message": str(e)}


# Background Library Sync State Tracking
_SYNC_LOCK = threading.Lock()
_sync_state = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "exit_code": None,
    "output_lines": [],
    "summary": "Idle"
}


def get_library_sync_status() -> dict:
    """Return live status and recent log output of background library synchronization."""
    with _SYNC_LOCK:
        return {
            "running": _sync_state["running"],
            "started_at": _sync_state["started_at"],
            "finished_at": _sync_state["finished_at"],
            "exit_code": _sync_state["exit_code"],
            "summary": _sync_state["summary"],
            "output": "\n".join(_sync_state["output_lines"][-35:])
        }


def _run_sync_worker(dry_run: bool = False):
    global _sync_state
    import sys
    app_root = Path(__file__).resolve().parent.parent
    sync_sh = app_root / "cli" / "sync-library.sh"
    sync_py = app_root / "cli" / "sync_library.py"
    if sync_sh.exists() and os.access(sync_sh, os.X_OK):
        cmd = [str(sync_sh)]
    else:
        cmd = [sys.executable, str(sync_py)]
    if dry_run:
        cmd.append("--dry-run")

    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _SYNC_LOCK:
        _sync_state["running"] = True
        _sync_state["started_at"] = start_time
        _sync_state["finished_at"] = ""
        _sync_state["exit_code"] = None
        _sync_state["output_lines"] = [f"=== Library Sync Started at {start_time} ==="]
        _sync_state["summary"] = "Syncing books in background..."

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        for line in iter(proc.stdout.readline, ''):
            clean_l = line.rstrip()
            if clean_l:
                with _SYNC_LOCK:
                    _sync_state["output_lines"].append(clean_l)
                    if len(_sync_state["output_lines"]) > 500:
                        _sync_state["output_lines"] = _sync_state["output_lines"][-500:]
        proc.wait()
        exit_code = proc.returncode
        finish_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _SYNC_LOCK:
            _sync_state["running"] = False
            _sync_state["finished_at"] = finish_time
            _sync_state["exit_code"] = exit_code
            if exit_code == 0:
                _sync_state["summary"] = f"Sync completed successfully at {finish_time}"
            else:
                _sync_state["summary"] = f"Sync completed with exit code {exit_code}"

        try:
            book_service.invalidate_caches()
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error in background library sync: {e}")
        with _SYNC_LOCK:
            _sync_state["running"] = False
            _sync_state["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _sync_state["exit_code"] = -1
            _sync_state["summary"] = f"Sync failed: {e}"
            _sync_state["output_lines"].append(f"ERROR: {e}")


def start_library_sync(dry_run: bool = False) -> dict:
    """Trigger library storage synchronization in a detached background thread."""
    with _SYNC_LOCK:
        if _sync_state["running"]:
            return {"success": False, "message": "Library synchronization is already running in the background."}

    t = threading.Thread(target=_run_sync_worker, args=(dry_run,), daemon=True)
    t.start()
    return {"success": True, "message": "Library sync initiated in the background."}


def get_backup_file_path(filename: str) -> Path | None:
    """Verify and return safe path to an existing backup file in BACKUP_DIR."""
    clean_name = Path(filename).name
    if not re.match(r"^booksdb_\d{8}_\d{6}\.sql\.gz$", clean_name):
        return None
    target = (BACKUP_DIR / clean_name).resolve()
    if target.parent != BACKUP_DIR.resolve() or not target.is_file():
        return None
    return target


def audit_library_files(db: Session, sample_size: int = 100) -> dict:
    """Verify that physical book files exist in LIBRARY_ROOT for a random sample of books."""
    rows = db.execute(text("""
        SELECT b.id, b.title, b.path, d.name, d.format 
        FROM books b 
        JOIN data d ON b.id = d.book_id 
        ORDER BY random() 
        LIMIT :limit
    """), {"limit": sample_size}).fetchall()

    lib_root = Path(settings.LIBRARY_ROOT)
    missing = []
    valid_count = 0

    for r in rows:
        expected_file = lib_root / r.path / f"{r.name}.{r.format.lower()}"
        if expected_file.exists():
            valid_count += 1
        else:
            missing.append({
                "book_id": r.id,
                "title": r.title,
                "expected_path": str(expected_file)
            })

    total_checked = len(rows)
    pct = round((valid_count / total_checked) * 100, 1) if total_checked else 100.0

    return {
        "success": True,
        "checked": total_checked,
        "valid": valid_count,
        "missing_count": len(missing),
        "health_percent": pct,
        "missing_samples": missing[:5],
        "message": f"Integrity check complete: {valid_count}/{total_checked} files verified ({pct}% healthy)."
    }


def get_system_status(db: Session) -> dict:
    """Fetch database health, backup files list, cache status, and storage metrics."""
    # 1. Database size & version
    db_size = "Unknown"
    db_version = "PostgreSQL"
    total_books = 0
    total_authors = 0
    try:
        size_row = db.execute(text("SELECT pg_size_pretty(pg_database_size(current_database())) as size")).fetchone()
        if size_row:
            db_size = size_row.size
        ver_row = db.execute(text("SELECT version() as ver")).fetchone()
        if ver_row:
            db_version = ver_row.ver.split(",")[0]
        total_books = db.execute(text("SELECT COUNT(*) FROM books")).scalar() or 0
        total_authors = db.execute(text("SELECT COUNT(*) FROM authors")).scalar() or 0
    except Exception as e:
        logger.warning(f"Failed to fetch DB metrics: {e}")

    # 2. Disk storage
    storage_info = {
        "path": settings.LIBRARY_ROOT,
        "total": "0 GB",
        "used": "0 GB",
        "free": "0 GB",
        "percent": 0
    }
    try:
        usage = shutil.disk_usage(settings.LIBRARY_ROOT)
        storage_info = {
            "path": settings.LIBRARY_ROOT,
            "total": f"{usage.total / (1024**4):.1f} TB" if usage.total > 1024**4 else f"{usage.total / (1024**3):.1f} GB",
            "used": f"{usage.used / (1024**4):.1f} TB" if usage.used > 1024**4 else f"{usage.used / (1024**3):.1f} GB",
            "free": f"{usage.free / (1024**4):.1f} TB" if usage.free > 1024**4 else f"{usage.free / (1024**3):.1f} GB",
            "percent": round((usage.used / usage.total) * 100, 1)
        }
    except Exception as e:
        logger.warning(f"Failed to read disk usage: {e}")

    # 3. Backup Status & files list
    backup_info = {
        "configured": BACKUP_DIR.is_dir(),
        "directory": str(BACKUP_DIR),
        "latest_file": "None found",
        "latest_time": "Never",
        "latest_size": "0 MB",
        "count": 0,
        "files": []
    }
    if BACKUP_DIR.is_dir():
        try:
            files = [f for f in BACKUP_DIR.glob("*.sql.gz") if not f.is_symlink()]
            if not files:
                files = [f for f in BACKUP_DIR.glob("*.dump") if not f.is_symlink()]
            if files:
                files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
                latest = files[0]
                stat = latest.stat()
                backup_info["latest_file"] = latest.name
                backup_info["latest_time"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                backup_info["latest_size"] = f"{stat.st_size / (1024**2):.1f} MB"
                backup_info["count"] = len(files)

                # Store top 10 recent backup files for the table
                for f in files[:10]:
                    fstat = f.stat()
                    backup_info["files"].append({
                        "name": f.name,
                        "size": f"{fstat.st_size / (1024**2):.1f} MB",
                        "time": datetime.fromtimestamp(fstat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "is_latest": f.name == latest.name
                    })
        except Exception as e:
            logger.warning(f"Error checking backups directory: {e}")

    # 4. Thumbnail cache
    thumb_cache = get_thumbnail_cache_status()

    # 5. Fail2ban live jail status
    f2b_status = get_fail2ban_status()

    # 6. Host system resources (CPU load, RAM, Uptime)
    cpu_load = "Unknown"
    try:
        l1, l5, l15 = os.getloadavg()
        cpu_load = f"{l1:.2f}, {l5:.2f}, {l15:.2f}"
    except Exception:
        pass

    ram_info = {"total": "Unknown", "used": "Unknown", "free": "Unknown", "percent": 0}
    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    k = parts[0].strip()
                    val = parts[1].strip().split()[0]
                    if val.isdigit():
                        mem[k] = int(val)
            if "MemTotal" in mem and "MemAvailable" in mem:
                tot = mem["MemTotal"]
                avail = mem["MemAvailable"]
                used = tot - avail
                ram_info = {
                    "total": f"{tot / (1024**2):.1f} GB",
                    "used": f"{used / (1024**2):.1f} GB",
                    "free": f"{avail / (1024**2):.1f} GB",
                    "percent": round((used / tot) * 100, 1)
                }
    except Exception:
        pass

    uptime_str = "Unknown"
    try:
        with open("/proc/uptime", "r") as f:
            up_secs = float(f.readline().split()[0])
            d = int(up_secs // 86400)
            h = int((up_secs % 86400) // 3600)
            m = int((up_secs % 3600) // 60)
            uptime_str = f"{d}d {h}h {m}m" if d > 0 else f"{h}h {m}m"
    except Exception:
        pass

    host_resources = {
        "cpu_load": cpu_load,
        "ram": ram_info,
        "uptime": uptime_str,
    }

    return {
        "db_size": db_size,
        "db_version": db_version,
        "total_books": total_books,
        "total_authors": total_authors,
        "storage": storage_info,
        "backup": backup_info,
        "thumb_cache": thumb_cache,
        "fail2ban": f2b_status,
        "host": host_resources,
        "sync": get_library_sync_status(),
        "auto_sync": get_auto_sync_status(),
        "env_path": str(get_target_env_path())
    }


# ===================================================================
# AUTOMATED LIBRARY SYNC & CRON SCHEDULER
# ===================================================================

AUTO_SYNC_CRON_PATH = Path("/etc/cron.d/bookserver_sync")


def get_auto_sync_status() -> dict:
    """Check if scheduled automated library sync is enabled and return schedule."""
    if not AUTO_SYNC_CRON_PATH.exists():
        return {
            "enabled": False,
            "schedule": "0 2 * * *",
            "hour": "2",
            "minute": "0",
            "description": "Disabled"
        }
    try:
        content = AUTO_SYNC_CRON_PATH.read_text(encoding="utf-8").strip()
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 6:
                    minute = parts[0]
                    hour = parts[1]
                    return {
                        "enabled": True,
                        "schedule": f"{minute} {hour} * * *",
                        "hour": hour,
                        "minute": minute,
                        "description": f"Daily at {hour.zfill(2)}:{minute.zfill(2)}"
                    }
    except Exception as e:
        logger.error(f"Error reading cron {AUTO_SYNC_CRON_PATH}: {e}")

    return {
        "enabled": False,
        "schedule": "0 2 * * *",
        "hour": "2",
        "minute": "0",
        "description": "Disabled"
    }


def set_auto_sync_schedule(enabled: bool, hour: int = 2, minute: int = 0) -> dict:
    """Enable or disable scheduled automated library rescan."""
    if not enabled:
        if AUTO_SYNC_CRON_PATH.exists():
            try:
                AUTO_SYNC_CRON_PATH.unlink()
            except Exception as e:
                logger.error(f"Failed to remove {AUTO_SYNC_CRON_PATH}: {e}")
                return {"success": False, "error": str(e)}
        return {"success": True, "enabled": False}

    cron_content = (
        f"# Automated Nightly Library Rescan for Athenea\n"
        f"SHELL=/bin/bash\n"
        f"PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n"
        f"{minute} {hour} * * * athenea /opt/athenea/venv/bin/python /opt/athenea/cli/sync_library.py >> /var/log/athenea_sync.log 2>&1\n"
    )
    try:
        AUTO_SYNC_CRON_PATH.write_text(cron_content, encoding="utf-8")
        try:
            os.chmod(AUTO_SYNC_CRON_PATH, 0o644)
        except Exception:
            pass
        return {
            "success": True,
            "enabled": True,
            "hour": str(hour),
            "minute": str(minute),
            "schedule": f"{minute} {hour} * * *"
        }
    except Exception as e:
        logger.error(f"Failed to write {AUTO_SYNC_CRON_PATH}: {e}")
        return {"success": False, "error": str(e)}


# ===================================================================
# DUPLICATE BOOK DETECTION
# ===================================================================

def find_duplicate_books(db: Session, limit: int = 50) -> list[dict]:
    """
    Find books with identical normalized titles and authors.
    Returns grouped list of duplicate book sets.
    """
    rows = db.execute(text("""
        WITH duplicate_groups AS (
            SELECT lower(trim(b.title)) AS clean_title,
                   lower(trim(coalesce(b.author_sort, ''))) AS clean_author,
                   count(*) AS cnt
            FROM books b
            WHERE b.title IS NOT NULL AND length(trim(b.title)) > 2
            GROUP BY lower(trim(b.title)), lower(trim(coalesce(b.author_sort, '')))
            HAVING count(*) > 1
            ORDER BY cnt DESC
            LIMIT :limit
        )
        SELECT
            dg.clean_title,
            dg.clean_author,
            dg.cnt,
            b.id,
            b.title,
            b.author_sort,
            b.series_index,
            b.has_cover,
            b.pubdate,
            b.path,
            (
                SELECT string_agg(d.format, ', ')
                FROM data d
                WHERE d.book_id = b.id
            ) AS formats
        FROM duplicate_groups dg
        JOIN books b ON lower(trim(b.title)) = dg.clean_title
                    AND lower(trim(coalesce(b.author_sort, ''))) = dg.clean_author
        ORDER BY dg.cnt DESC, dg.clean_title, b.id ASC
    """), {"limit": limit}).fetchall()

    groups = {}
    for r in rows:
        key = (r.clean_title, r.clean_author)
        if key not in groups:
            groups[key] = {
                "title": r.title,
                "author": r.author_sort or "Unknown",
                "count": r.cnt,
                "books": []
            }
        groups[key]["books"].append({
            "id": r.id,
            "title": r.title,
            "author": r.author_sort,
            "has_cover": bool(r.has_cover),
            "thumbnail_url": f"/thumbnail/{r.id}",
            "pubdate": r.pubdate.strftime("%Y-%m-%d") if r.pubdate else "Unknown",
            "path": r.path,
            "formats": r.formats or "None"
        })

    return list(groups.values())


# ===================================================================
# COVER ART REPLACER & OPENLIBRARY FETCHER
# ===================================================================

def replace_book_cover(db: Session, book_id: int, image_bytes: bytes) -> dict:
    """
    Replace a book's cover on disk and update database/thumbnail cache.
    """
    import io
    from PIL import Image

    row = db.execute(
        text("SELECT id, path, has_cover FROM books WHERE id = :id"),
        {"id": book_id}
    ).fetchone()
    if not row or not row.path:
        return {"success": False, "error": "Book not found or has invalid file path"}

    lib_root = Path(settings.LIBRARY_ROOT)
    book_dir = lib_root / row.path
    if not book_dir.exists() or not book_dir.is_dir():
        return {"success": False, "error": f"Book folder {row.path} does not exist on disk"}

    cover_file = book_dir / "cover.jpg"

    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(str(cover_file), format="JPEG", quality=90, optimize=True)

        if not row.has_cover:
            db.execute(text("UPDATE books SET has_cover = true WHERE id = :id"), {"id": book_id})
            db.commit()

        thumb_file = Path(settings.THUMB_CACHE_DIR) / f"{book_id}.webp"
        if thumb_file.exists():
            thumb_file.unlink(missing_ok=True)
        try:
            img_thumb = img.copy()
            img_thumb.thumbnail((240, 360), Image.Resampling.LANCZOS)
            img_thumb.save(str(thumb_file), format="WEBP", quality=80, method=4)
        except Exception as te:
            logger.warning(f"Could not write thumb for #{book_id}: {te}")

        book_service.invalidate_caches()
        timestamp = int(datetime.now().timestamp())
        return {
            "success": True,
            "book_id": book_id,
            "cover_url": f"/cover/{book_id}?v={timestamp}",
            "thumbnail_url": f"/thumbnail/{book_id}?v={timestamp}"
        }
    except Exception as e:
        logger.error(f"Error replacing cover for #{book_id}: {e}")
        return {"success": False, "error": str(e)}


def fetch_cover_from_openlibrary(db: Session, book_id: int) -> dict:
    """
    Download a cover for the book from OpenLibrary by ISBN or Title+Author.
    """
    import urllib.request
    import urllib.parse
    import json

    row = db.execute(text("""
        SELECT b.id, b.title, b.isbn, b.author_sort, b.path,
               (SELECT a.name FROM book_authors ba JOIN authors a ON ba.author_id = a.id WHERE ba.book_id = b.id LIMIT 1) AS author_name
        FROM books b
        WHERE b.id = :id
    """), {"id": book_id}).fetchone()
    if not row:
        return {"success": False, "error": "Book not found"}

    image_bytes = None

    # 1. Try ISBN lookup
    if row.isbn and row.isbn.strip():
        clean_isbn = re.sub(r"[^0-9X]", "", row.isbn.upper().strip())
        if clean_isbn:
            url = f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-L.jpg?default=false"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "BookServer/2.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status == 200:
                        data = resp.read()
                        if len(data) > 1000:
                            image_bytes = data
            except Exception:
                pass

    # 2. Try Title + Author search
    if not image_bytes and row.title:
        query_author = row.author_name or row.author_sort or ""
        q_str = f"{row.title} {query_author}".strip()
        search_q = urllib.parse.quote(q_str)
        api_url = f"https://openlibrary.org/search.json?q={search_q}&limit=3"
        try:
            req = urllib.request.Request(api_url, headers={"User-Agent": "BookServer/2.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    ol_data = json.loads(resp.read().decode("utf-8"))
                    docs = ol_data.get("docs", [])
                    for doc in docs:
                        cover_i = doc.get("cover_i")
                        if cover_i:
                            c_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg?default=false"
                            c_req = urllib.request.Request(c_url, headers={"User-Agent": "BookServer/2.0"})
                            with urllib.request.urlopen(c_req, timeout=10) as c_resp:
                                if c_resp.status == 200:
                                    c_data = c_resp.read()
                                    if len(c_data) > 1000:
                                        image_bytes = c_data
                                        break
        except Exception as se:
            logger.warning(f"OpenLibrary query failed for #{book_id}: {se}")

    if not image_bytes:
        return {"success": False, "error": "No high-resolution cover was found on Open Library for this book."}

    return replace_book_cover(db, book_id, image_bytes)


# ===================================================================
# ADMIN-ONLY BOOK DELETION (DB + FILES + CACHE)
# ===================================================================

def delete_book(db: Session, book_id: int, delete_files: bool = True) -> dict:
    """
    Permanently delete a book from the PostgreSQL database (including all related
    links, formats, user shelves/progress/notes) and optionally remove its files
    from LIBRARY_ROOT and the WebP thumbnail cache.
    """
    row = db.execute(
        text("SELECT id, title, author_sort, path FROM books WHERE id = :id"),
        {"id": book_id}
    ).fetchone()
    if not row:
        return {"success": False, "error": f"Book #{book_id} not found"}

    book_title = row.title
    book_author = row.author_sort or "Unknown"
    book_rel_path = row.path

    # Capture linked entity IDs before deleting link rows so we can prune orphans
    author_ids = [r[0] for r in db.execute(text("SELECT author_id FROM book_authors WHERE book_id = :id"), {"id": book_id}).fetchall()]
    publisher_ids = [r[0] for r in db.execute(text("SELECT publisher_id FROM book_publishers WHERE book_id = :id"), {"id": book_id}).fetchall()]
    series_ids = [r[0] for r in db.execute(text("SELECT series_id FROM book_series WHERE book_id = :id"), {"id": book_id}).fetchall()]
    tag_ids = [r[0] for r in db.execute(text("SELECT tag_id FROM book_tags WHERE book_id = :id"), {"id": book_id}).fetchall()]

    try:
        # 1. Remove all dependent rows across link & user tables
        for tbl in (
            "book_authors",
            "book_publishers",
            "book_series",
            "book_tags",
            "book_languages",
            "comments",
            "data",
            "identifiers",
            "favorites",
            "reading_progress",
            "reading_status",
            "user_bookmarks",
            "user_notes",
        ):
            exists = db.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
                {"t": tbl}
            ).fetchone()
            if exists:
                db.execute(text(f"DELETE FROM {tbl} WHERE book_id = :id"), {"id": book_id})

        # 2. Delete the book row itself
        db.execute(text("DELETE FROM books WHERE id = :id"), {"id": book_id})

        # 3. Clean up orphaned authors, series, publishers, and tags that have 0 remaining books
        for aid in author_ids:
            rem = db.execute(text("SELECT 1 FROM book_authors WHERE author_id = :aid LIMIT 1"), {"aid": aid}).fetchone()
            if not rem:
                db.execute(text("DELETE FROM authors WHERE id = :aid"), {"aid": aid})

        for pid in publisher_ids:
            rem = db.execute(text("SELECT 1 FROM book_publishers WHERE publisher_id = :pid LIMIT 1"), {"pid": pid}).fetchone()
            if not rem:
                db.execute(text("DELETE FROM publishers WHERE id = :pid"), {"pid": pid})

        for sid in series_ids:
            rem = db.execute(text("SELECT 1 FROM book_series WHERE series_id = :sid LIMIT 1"), {"sid": sid}).fetchone()
            if not rem:
                db.execute(text("DELETE FROM series WHERE id = :sid"), {"sid": sid})

        for tid in tag_ids:
            rem = db.execute(text("SELECT 1 FROM book_tags WHERE tag_id = :tid LIMIT 1"), {"tid": tid}).fetchone()
            if not rem:
                db.execute(text("DELETE FROM tags WHERE id = :tid"), {"tid": tid})

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Database error deleting book #{book_id}: {e}")
        return {"success": False, "error": f"Database error: {e}"}

    # 4. Remove cached WebP thumbnail
    try:
        thumb_file = Path(settings.THUMB_CACHE_DIR) / f"{book_id}.webp"
        if thumb_file.exists():
            thumb_file.unlink(missing_ok=True)
    except Exception as te:
        logger.warning(f"Failed to remove thumbnail cache for #{book_id}: {te}")

    # 5. Optionally remove physical book directory from LIBRARY_ROOT
    files_deleted = False
    if delete_files and book_rel_path and book_rel_path.strip():
        try:
            lib_root = Path(settings.LIBRARY_ROOT).resolve()
            book_dir = (lib_root / book_rel_path).resolve()
            # Security check: ensure book_dir is strictly a subdirectory inside lib_root
            if lib_root in book_dir.parents and book_dir != lib_root and book_dir.exists() and book_dir.is_dir():
                shutil.rmtree(book_dir, ignore_errors=True)
                files_deleted = True
                # If parent author folder is now empty and inside lib_root, clean it up too
                author_dir = book_dir.parent
                if author_dir != lib_root and lib_root in author_dir.parents and author_dir.exists():
                    try:
                        if not any(author_dir.iterdir()):
                            author_dir.rmdir()
                    except Exception:
                        pass
        except Exception as fe:
            logger.warning(f"Failed to delete physical directory for book #{book_id} ({book_rel_path}): {fe}")

    # 6. Invalidate catalog & stats caches
    try:
        book_service.invalidate_caches()
    except Exception:
        pass

    return {
        "success": True,
        "book_id": book_id,
        "title": book_title,
        "author": book_author,
        "files_deleted": files_deleted,
        "message": f"Deleted '{book_title}' (#{book_id})" + (" and removed files from disk." if files_deleted else " from catalog database.")
    }


