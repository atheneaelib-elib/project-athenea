"""
Backfill metadata (publication date, publisher, tags, description) for existing library books.
Runs locally or inside the Athenea LXC/Docker environment.
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from sqlalchemy import text

# Ensure app root is in sys.path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from settings import settings
from db import SessionLocal
from services.importer import parse_fb2_metadata, parse_epub_metadata, parse_pubdate


def run_backfill():
    print(f"[*] Starting Athenea Metadata Backfill...")
    print(f"[*] Library root: {settings.LIBRARY_ROOT}")

    lib_path = Path(settings.LIBRARY_ROOT)
    if not lib_path.is_dir():
        print(f"[!] Error: Library root {lib_path} does not exist!")
        return

    updated_pubdate_count = 0
    updated_publisher_count = 0

    db = SessionLocal()
    try:
        books = db.execute(text("""
            SELECT b.id, b.title, b.path, b.pubdate,
                   (SELECT p.name FROM publishers p 
                    JOIN book_publishers bp ON p.id = bp.publisher_id 
                    WHERE bp.book_id = b.id LIMIT 1) AS current_publisher
            FROM books b
            ORDER BY b.id
        """)).fetchall()

        print(f"[*] Found {len(books)} books in catalog.")

        for b in books:
            book_dir = lib_path / b.path
            if not book_dir.is_dir():
                print(f"[-] Book #{b.id} ('{b.title}'): directory {book_dir} not found.")
                continue

            # Find source book file (prefer original FB2, then EPUB)
            book_file = None
            meta = {}

            # Look for fb2 first
            fb2_files = list(book_dir.glob("*.fb2"))
            if fb2_files:
                book_file = fb2_files[0]
                meta = parse_fb2_metadata(book_file)
            else:
                epub_files = list(book_dir.glob("*.epub"))
                if epub_files:
                    book_file = epub_files[0]
                    meta = parse_epub_metadata(book_file)

            if not book_file:
                print(f"[-] Book #{b.id} ('{b.title}'): no FB2 or EPUB found in {book_dir}")
                continue

            updates = []
            
            # 1. Update pubdate if NULL
            if b.pubdate is None and meta.get("pubdate"):
                dt = parse_pubdate(meta["pubdate"])
                if dt:
                    db.execute(text("""
                        UPDATE books SET pubdate = :dt WHERE id = :bid
                    """), {"dt": dt, "bid": b.id})
                    updated_pubdate_count += 1
                    updates.append(f"pubdate -> {dt.strftime('%Y-%m-%d')}")

            # 2. Update publisher if NULL
            if not b.current_publisher and meta.get("publisher"):
                p_name = meta["publisher"].strip()
                if p_name:
                    p_row = db.execute(
                        text("SELECT id FROM publishers WHERE LOWER(name) = LOWER(:p) LIMIT 1"),
                        {"p": p_name}
                    ).fetchone()
                    if p_row:
                        pid = p_row.id
                    else:
                        pid = db.execute(text("""
                            INSERT INTO publishers (name) VALUES (:p) RETURNING id
                        """), {"p": p_name}).scalar()

                    db.execute(text("""
                        INSERT INTO book_publishers (book_id, publisher_id)
                        VALUES (:bid, :pid)
                        ON CONFLICT (book_id, publisher_id) DO NOTHING
                    """), {"bid": b.id, "pid": pid})
                    updated_publisher_count += 1
                    updates.append(f"publisher -> '{p_name}'")

            # 3. Update comments/description if missing
            if meta.get("comments"):
                has_comm = db.execute(text("SELECT 1 FROM comments WHERE book_id = :bid"), {"bid": b.id}).fetchone()
                if not has_comm:
                    db.execute(text("""
                        INSERT INTO comments (book_id, text) VALUES (:bid, :txt)
                        ON CONFLICT (book_id) DO UPDATE SET text = EXCLUDED.text
                    """), {"bid": b.id, "txt": meta["comments"].strip()})
                    updates.append("description added")

            if updates:
                db.commit()
                print(f"[+] Book #{b.id} ('{b.title}'): {', '.join(updates)}")
            else:
                current_pub = b.pubdate.strftime('%Y-%m-%d') if b.pubdate else "None"
                print(f"[=] Book #{b.id} ('{b.title}'): already up to date (pubdate={current_pub}, publisher={b.current_publisher or 'None'})")

    finally:
        db.close()

    print("\n" + "=" * 50)
    print(f"[*] Backfill Complete!")
    print(f"[*] Total books updated with pubdate:   {updated_pubdate_count}")
    print(f"[*] Total books updated with publisher: {updated_publisher_count}")
    print("=" * 50)


if __name__ == "__main__":
    run_backfill()
