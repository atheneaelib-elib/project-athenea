#!/usr/bin/env python3
"""
Backfill metadata (publication date, publisher, series, tags, description, cover.jpg)
for existing library books.
Runs locally or inside the Athenea / Bookserver LXC/Docker environment.
"""

import sys
import os
from pathlib import Path
from datetime import datetime
from sqlalchemy import text

# Ensure app root (/opt/athenea) is in sys.path
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from settings import settings
from db import SessionLocal
from services.importer import parse_fb2_metadata, parse_epub_metadata, parse_pubdate


def run_backfill():
    print("[*] Starting Athenea Metadata Backfill...")
    print(f"[*] Library root: {settings.LIBRARY_ROOT}")

    lib_path = Path(settings.LIBRARY_ROOT)
    if not lib_path.is_dir():
        print(f"[!] Error: Library root {lib_path} does not exist!")
        return

    updated_pubdate_count = 0
    updated_publisher_count = 0
    updated_cover_count = 0

    db = SessionLocal()
    try:
        books = db.execute(text("""
            SELECT b.id, b.title, b.path, b.pubdate, b.has_cover,
                   (SELECT p.name FROM publishers p 
                    JOIN book_publishers bp ON p.id = bp.publisher_id 
                    WHERE bp.book_id = b.id LIMIT 1) AS current_publisher,
                   (SELECT s.name FROM series s 
                    JOIN book_series bs ON s.id = bs.series_id 
                    WHERE bs.book_id = b.id LIMIT 1) AS current_series
            FROM books b
            ORDER BY b.id
        """)).fetchall()

        print(f"[*] Found {len(books)} books in catalog.")

        for b in books:
            if not b.path:
                continue
            book_dir = lib_path / b.path
            if not book_dir.is_dir():
                print(f"[-] Book #{b.id} ('{b.title}'): directory {book_dir} not found.")
                continue

            # Find source book file (prefer original FB2, then EPUB)
            book_file = None
            meta = {}

            fb2_files = list(book_dir.glob("*.fb2"))
            if fb2_files:
                book_file = fb2_files[0]
                meta = parse_fb2_metadata(book_file)

            epub_files = list(book_dir.glob("*.epub"))
            if epub_files:
                if not book_file:
                    book_file = epub_files[0]
                    meta = parse_epub_metadata(book_file)
                elif not meta.get("pubdate") or not meta.get("publisher") or not meta.get("cover_bytes"):
                    ep_meta = parse_epub_metadata(epub_files[0])
                    for k, v in ep_meta.items():
                        if v and not meta.get(k):
                            meta[k] = v

            if not book_file:
                print(f"[-] Book #{b.id} ('{b.title}'): no FB2 or EPUB found in {book_dir}")
                continue

            updates = []

            # 1. Update pubdate if NULL or dummy year <= 101
            if (b.pubdate is None or b.pubdate.year <= 101) and meta.get("pubdate"):
                dt = parse_pubdate(meta["pubdate"])
                if dt and dt.year > 101:
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

            # 3. Update series if NULL
            if not b.current_series and meta.get("series"):
                s_name = meta["series"].strip()
                if s_name:
                    s_row = db.execute(
                        text("SELECT id FROM series WHERE LOWER(name) = LOWER(:s) LIMIT 1"),
                        {"s": s_name}
                    ).fetchone()
                    if s_row:
                        sid = s_row.id
                    else:
                        sid = db.execute(text("""
                            INSERT INTO series (name) VALUES (:s) RETURNING id
                        """), {"s": s_name}).scalar()

                    db.execute(text("""
                        INSERT INTO book_series (book_id, series_id)
                        VALUES (:bid, :sid)
                        ON CONFLICT (book_id, series_id) DO NOTHING
                    """), {"bid": b.id, "sid": sid})
                    if meta.get("series_index"):
                        db.execute(text("UPDATE books SET series_index = :sidx WHERE id = :bid"), {"sidx": meta["series_index"], "bid": b.id})
                    updates.append(f"series -> '{s_name}'")

            # 4. Update comments/description if missing
            if meta.get("comments") and meta["comments"].strip():
                has_comm = db.execute(text("SELECT 1 FROM comments WHERE book_id = :bid"), {"bid": b.id}).fetchone()
                if not has_comm:
                    db.execute(text("""
                        INSERT INTO comments (book_id, text) VALUES (:bid, :txt)
                        ON CONFLICT (book_id) DO UPDATE SET text = EXCLUDED.text
                    """), {"bid": b.id, "txt": meta["comments"].strip()})
                    updates.append("description added")

            # 5. Update cover.jpg if missing
            cover_path = book_dir / "cover.jpg"
            if (not b.has_cover or not cover_path.is_file()) and meta.get("cover_bytes") and len(meta["cover_bytes"]) > 100:
                try:
                    cover_path.write_bytes(meta["cover_bytes"])
                    db.execute(text("UPDATE books SET has_cover = 1 WHERE id = :bid"), {"bid": b.id})
                    updated_cover_count += 1
                    updates.append("cover extracted")
                except Exception:
                    pass

            if updates:
                db.commit()
                print(f"[+] Book #{b.id} ('{b.title}'): {', '.join(updates)}")
            else:
                current_pub = b.pubdate.strftime('%Y-%m-%d') if b.pubdate else "None"
                print(f"[=] Book #{b.id} ('{b.title}'): already up to date (pubdate={current_pub}, publisher={b.current_publisher or 'None'})")

    finally:
        db.close()

    print("\n" + "=" * 50)
    print("[*] Backfill Complete!")
    print(f"[*] Total books updated with pubdate:   {updated_pubdate_count}")
    print(f"[*] Total books updated with publisher: {updated_publisher_count}")
    print(f"[*] Total books updated with cover:     {updated_cover_count}")
    print("=" * 50)


if __name__ == "__main__":
    run_backfill()
