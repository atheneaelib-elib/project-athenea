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

PRIMARY_ENV_PATH = Path("/etc/bookserver.env")
FALLBACK_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_PROD_BACKUP = Path("/mnt/mainlib/Backups/db")
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "/mnt/mainlib/Backups/db" if _PROD_BACKUP.exists() else str(Path(__file__).resolve().parent.parent / "backups")))
BACKUP_SCRIPT = Path("/opt/bookserver/backup_db.sh")


def get_target_env_path() -> Path:
    """Return the active configuration file path."""
    if PRIMARY_ENV_PATH.exists() or os.access(PRIMARY_ENV_PATH.parent, os.W_OK):
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


def update_env_file(key_values: dict[str, str]) -> Path:
    """
    Update or append key=value pairs in the configuration file,
    preserving comments and existing formatting.
    """
    env_path, lines = read_raw_env_file()
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

    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.error(f"Failed to write configuration file {env_path}: {e}")
        raise RuntimeError(f"Unable to write to {env_path}: {e}")

    for k, v in key_values.items():
        os.environ[k] = str(v)

    return env_path


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
    user = (user or "").strip()
    from_addr = (from_addr or "").strip() or getattr(settings, "SMTP_FROM", "books@example.com")
    port = int(port) if str(port).isdigit() else 587

    if not password or password in ("••••••••", "********"):
        effective_password = settings.SMTP_PASSWORD
    else:
        effective_password = password.strip()

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
    user = (user or "").strip()
    from_addr = (from_addr or "").strip() or getattr(settings, "SMTP_FROM", "books@example.com")

    if not password or password in ("••••••••", "********"):
        password = settings.SMTP_PASSWORD

    use_ssl = ssl or port == 465

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)

        with server:
            server.ehlo()
            if tls and not use_ssl:
                server.starttls()
                server.ehlo()

            if user and password:
                server.login(user, password)

            if test_recipient and test_recipient.strip():
                recipient = test_recipient.strip()
                msg = EmailMessage()
                msg["Subject"] = "eBook Library - SMTP Test Email"
                msg["From"] = from_addr
                msg["To"] = recipient
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                msg.set_content(
                    f"Hello,\n\n"
                    f"This is a confirmation test email from your eBook Library (BookServer).\n"
                    f"Timestamp: {timestamp}\n"
                    f"SMTP Host: {host}:{port}\n"
                    f"Sender: {from_addr}\n\n"
                    f"If you received this message, 'Send to Kindle' and email delivery are working perfectly!\n"
                )
                server.send_message(msg)
                return {
                    "success": True,
                    "message": f"Success! Handshake, authentication, and test email sent to {recipient}."
                }

        return {
            "success": True,
            "message": f"Success! Connected to {host}:{port} and authenticated successfully."
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
    cmd = ["/opt/bookserver/api/sync-library"]
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
        f"# Automated Nightly Library Rescan for BookServer\n"
        f"SHELL=/bin/bash\n"
        f"PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin\n"
        f"{minute} {hour} * * * books /opt/bookserver/api/venv/bin/python /opt/bookserver/api/cli/sync_library.py >> /var/log/bookserver_sync.log 2>&1\n"
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

