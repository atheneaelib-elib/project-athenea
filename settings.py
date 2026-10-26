import os
from pathlib import Path
from pydantic import BaseModel


def _load_env_files():
    for env_path in [
        Path("/etc/athenea.env"),
        Path("/etc/bookserver.env"),
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent / ".env"
    ]:
        if env_path.is_file():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass


_load_env_files()


class Settings(BaseModel):
    APP_NAME: str = os.getenv("APP_NAME", "Athenea")
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "athenea")
    DB_USER: str = os.getenv("DB_USER", "athenea")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    LIBRARY_ROOT: str = os.getenv("LIBRARY_ROOT", "/data/books")
    BACKUP_DIR: str = os.getenv("BACKUP_DIR", "/data/backups")
    PAGE_SIZE: int = 36

    # Security settings
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1")
    LOGIN_RATE_LIMIT_ATTEMPTS: int = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
    LOGIN_RATE_LIMIT_WINDOW: int = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW", "300"))  # 5 minutes
    ENABLE_DOCS: bool = os.getenv("ENABLE_DOCS", "false").lower() in ("true", "1")

    # User Registration settings
    ALLOW_REGISTRATION: bool = os.getenv("ALLOW_REGISTRATION", "true").lower() in ("true", "1")
    REQUIRE_ADMIN_APPROVAL: bool = os.getenv("REQUIRE_ADMIN_APPROVAL", "true").lower() in ("true", "1")

    # Cache & File storage
    THUMB_CACHE_DIR: str = os.getenv("THUMB_CACHE_DIR", "/var/cache/athenea/thumbs")

    # SMTP / Send to Kindle / Email settings
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "books@example.com")
    SMTP_TLS: bool = os.getenv("SMTP_TLS", "true").lower() in ("true", "1")
    SMTP_SSL: bool = os.getenv("SMTP_SSL", "false").lower() in ("true", "1")


settings = Settings()
