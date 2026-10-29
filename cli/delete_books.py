#!/usr/bin/env python3
"""
Athenea Admin-Only CLI Book Deletion Tool
Safely deletes books from the PostgreSQL catalog, cleans up orphaned metadata
(authors, series, publishers, tags), removes cached WebP cover thumbnails, and
deletes book files from LIBRARY_ROOT.

Security:
  - Enforces OS-level user check (root / athenea service account)
  - Authenticates against the Athenea database to verify the operator holds
    Administrator role (`admin`) or `can_delete_books` permission (or is UID 0
    root on the host with `--root-bypass`).
"""

import os
import sys
import getpass
import argparse
from pathlib import Path
from typing import List

# Ensure project root (/opt/athenea) is importable
APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from sqlalchemy import text
import db as db_module
from settings import settings
from services import auth as auth_service
from services import admin_settings as admin_settings_service


def verify_os_user() -> bool:
    """Ensure the CLI is executed by root or the athenea service user on POSIX."""
    if os.name != "posix":
        return True
    uid = os.geteuid()
    if uid == 0:
        return True
    try:
        import pwd
        uname = pwd.getpwuid(uid).pw_name
        if uname in ("athenea", "books"):
            return True
    except Exception:
        pass
    return False


def verify_admin_credentials(db, admin_user: str | None, admin_password: str | None, root_bypass: bool = False) -> dict:
    """
    Authenticate the CLI operator against the Athenea users table and ensure
    they have `is_admin` or `can_delete_books` permission.
    """
    auth_service.init_auth_tables(db)

    if root_bypass and os.name == "posix" and os.geteuid() == 0:
        admin_row = db.execute(
            text("SELECT id, username, role FROM users WHERE role = 'admin' AND is_active = TRUE ORDER BY id ASC LIMIT 1")
        ).fetchone()
        username = admin_row.username if admin_row else "root"
        return {"username": username, "role": "admin", "is_admin": True, "can_delete_books": True}

    username = admin_user or os.getenv("ATHENEA_ADMIN_USER")
    password = admin_password or os.getenv("ATHENEA_ADMIN_PASSWORD")

    if not username:
        try:
            username = input("Admin username: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[!] Authentication cancelled.", file=sys.stderr)
            sys.exit(1)

    if not password:
        try:
            password = getpass.getpass(f"Password for '{username}': ")
        except (EOFError, KeyboardInterrupt):
            print("\n[!] Authentication cancelled.", file=sys.stderr)
            sys.exit(1)

    user = auth_service.authenticate_user(db, username, password)
    if not user:
        print("[!] Authentication failed: Invalid username or password.", file=sys.stderr)
        sys.exit(1)

    if not (user.get("is_admin") or user.get("can_delete_books")):
        print(
            f"[!] Permission denied: User '{user['username']}' (role: {user['role']}) "
            f"does not have administrator / book deletion privileges.",
            file=sys.stderr,
        )
        sys.exit(1)

    return user


def resolve_target_books(db, ids: List[int] | None, search: str | None, author: str | None, path_match: str | None) -> List[dict]:
    """Find matching books in PostgreSQL based on CLI filter arguments."""
    results = {}

    if ids:
        rows = db.execute(
            text("""
                SELECT b.id, b.title, b.author_sort, b.path,
                        COALESCE(string_agg(DISTINCT d.format, ', '), 'NONE') AS formats
                FROM books b
                LEFT JOIN data d ON b.id = d.book_id
                WHERE b.id = ANY(:ids)
                GROUP BY b.id, b.title, b.author_sort, b.path
                ORDER BY b.id ASC
            """),
            {"ids": list(ids)},
        ).fetchall()
        for r in rows:
            results[r.id] = dict(r._mapping)

    if search:
        q = f"%{search.strip()}%"
        rows = db.execute(
            text("""
                SELECT b.id, b.title, b.author_sort, b.path,
                       COALESCE(string_agg(DISTINCT d.format, ', '), 'NONE') AS formats
                FROM books b
                LEFT JOIN data d ON b.id = d.book_id
                WHERE b.title ILIKE :q OR b.author_sort ILIKE :q
                GROUP BY b.id, b.title, b.author_sort, b.path
                ORDER BY b.id ASC
                LIMIT 200
            """),
            {"q": q},
        ).fetchall()
        for r in rows:
            results[r.id] = dict(r._mapping)

    if author:
        q = f"%{author.strip()}%"
        rows = db.execute(
            text("""
                SELECT b.id, b.title, b.author_sort, b.path,
                       COALESCE(string_agg(DISTINCT d.format, ', '), 'NONE') AS formats
                FROM books b
                LEFT JOIN data d ON b.id = d.book_id
                WHERE b.author_sort ILIKE :q
                GROUP BY b.id, b.title, b.author_sort, b.path
                ORDER BY b.id ASC
                LIMIT 200
            """),
            {"q": q},
        ).fetchall()
        for r in rows:
            results[r.id] = dict(r._mapping)

    if path_match:
        clean_p = path_match.strip()
        lib_root = str(Path(settings.LIBRARY_ROOT).resolve())
        if clean_p.startswith(lib_root):
            clean_p = clean_p[len(lib_root):].lstrip("/\\")
        q = f"%{clean_p}%"
        rows = db.execute(
            text("""
                SELECT b.id, b.title, b.author_sort, b.path,
                       COALESCE(string_agg(DISTINCT d.format, ', '), 'NONE') AS formats
                FROM books b
                LEFT JOIN data d ON b.id = d.book_id
                WHERE b.path ILIKE :q
                GROUP BY b.id, b.title, b.author_sort, b.path
                ORDER BY b.id ASC
                LIMIT 200
            """),
            {"q": q},
        ).fetchall()
        for r in rows:
            results[r.id] = dict(r._mapping)

    return [results[k] for k in sorted(results.keys())]


def main():
    parser = argparse.ArgumentParser(
        description="Athenea Admin-Only CLI Book Deletion Tool"
    )
    parser.add_argument(
        "book_ids",
        nargs="*",
        type=int,
        help="Book ID(s) to delete (e.g. 123 456)",
    )
    parser.add_argument(
        "--id", "-i",
        dest="flag_ids",
        type=int,
        action="append",
        default=[],
        help="Book ID to delete (can be specified multiple times)",
    )
    parser.add_argument(
        "--search", "-s",
        type=str,
        default=None,
        help="Find books matching title or author substring",
    )
    parser.add_argument(
        "--author", "-a",
        type=str,
        default=None,
        help="Find books matching author substring",
    )
    parser.add_argument(
        "--path", "-p",
        type=str,
        default=None,
        help="Find books matching library path",
    )
    parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Only remove book records from PostgreSQL; keep book files on disk",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview books and disk paths that would be deleted without making changes",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip interactive confirmation prompt",
    )
    parser.add_argument(
        "--admin-user", "-u",
        type=str,
        default=None,
        help="Admin username (or set ATHENEA_ADMIN_USER)",
    )
    parser.add_argument(
        "--admin-password",
        type=str,
        default=None,
        help="Admin password (or set ATHENEA_ADMIN_PASSWORD)",
    )
    parser.add_argument(
        "--root-bypass",
        action="store_true",
        help="Allow root (UID 0) to run non-interactively without password prompt",
    )

    args = parser.parse_args()
    all_ids = sorted(set((args.book_ids or []) + (args.flag_ids or [])))

    if not all_ids and not args.search and not args.author and not args.path:
        parser.print_help()
        print("\n[!] Error: Specify at least one Book ID, --search, --author, or --path.", file=sys.stderr)
        sys.exit(1)

    if not verify_os_user():
        print(
            "[!] Permission denied: This CLI tool can only be run by 'root' or the 'athenea' service user.",
            file=sys.stderr,
        )
        sys.exit(1)

    db = db_module.SessionLocal()
    try:
        operator = verify_admin_credentials(
            db,
            admin_user=args.admin_user,
            admin_password=args.admin_password,
            root_bypass=args.root_bypass,
        )
        print("=" * 78)
        print(" ATHENEA ADMIN BOOK DELETION CLI")
        print(f" Operator: {operator['username']} (role: {operator['role']})")
        print(f" Database: {settings.DB_NAME} @ {settings.DB_HOST}:{settings.DB_PORT}")
        print(f" Mode:     {'DRY RUN (No changes)' if args.dry_run else ('DB ONLY (Keep files)' if args.keep_files else 'FULL DELETE (DB + Disk Files)')}")
        print("=" * 78)

        targets = resolve_target_books(db, all_ids, args.search, args.author, args.path)
        if not targets:
            print("[!] No matching books found in catalog.")
            sys.exit(0)

        print(f"\nFound {len(targets)} matching book(s):")
        lib_root = Path(settings.LIBRARY_ROOT).resolve()
        for b in targets:
            full_path = (lib_root / b["path"]).resolve() if b.get("path") else None
            exists_str = "EXISTS" if (full_path and full_path.exists()) else "MISSING"
            print(
                f"  [#{b['id']}] {b['title']} — {b['author_sort'] or 'Unknown'} "
                f"[{b['formats']}] | Path: {b.get('path')} ({exists_str})"
            )

        if args.dry_run:
            print(f"\n[DRY-RUN] Would delete {len(targets)} book(s). No changes made.")
            sys.exit(0)

        if not args.yes:
            try:
                ans = input(
                    f"\nPermanently delete {len(targets)} book(s) "
                    f"{'(keeping disk files)' if args.keep_files else '(including files on disk)'}? [y/N]: "
                ).strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n[!] Aborted.")
                sys.exit(1)
            if ans not in ("y", "yes"):
                print("[*] Aborted. No books were deleted.")
                sys.exit(0)

        deleted_count = 0
        failed_count = 0
        for b in targets:
            res = admin_settings_service.delete_book(
                db,
                book_id=b["id"],
                delete_files=not args.keep_files,
            )
            if res.get("success"):
                deleted_count += 1
                print(f"  [OK] {res.get('message')}")
            else:
                failed_count += 1
                print(f"  [ERR] Book #{b['id']}: {res.get('error')}", file=sys.stderr)

        print("-" * 78)
        print(f"Completed: {deleted_count} deleted, {failed_count} failed.")
        if failed_count > 0:
            sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
