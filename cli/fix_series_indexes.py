#!/usr/bin/env python3
"""
High-Speed Series Index Repair for Athenea
==========================================
Scans books linked to a series whose series_index is currently 1.0 or NULL,
inspects their metadata.opf (and EPUB/FB2 containers) on disk in parallel,
extracts the true calibre:series_index / group-position / sequence number,
and updates books.series_index in PostgreSQL.

Usage:
    /opt/athenea/venv/bin/python -m cli.fix_series_indexes
    /opt/athenea/venv/bin/python -m cli.fix_series_indexes --all
    /opt/athenea/venv/bin/python -m cli.fix_series_indexes --dry-run
"""

import sys
import os
import re
import time
import argparse
import html
import zipfile
import multiprocessing as mp
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import psycopg2
from psycopg2.extras import DictCursor, execute_batch

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from settings import settings


def get_db_connection():
    conn = psycopg2.connect(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        dbname=settings.DB_NAME,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        cursor_factory=DictCursor
    )
    conn.set_client_encoding('UTF8')
    return conn


def extract_series_index_from_opf(opf_path: Path) -> Optional[float]:
    """Extract calibre:series_index from a metadata.opf file."""
    try:
        raw = opf_path.read_bytes()
        # Look for calibre:series_index meta tag
        m = re.search(
            rb'<meta[^>]+(?:name=[\'"](?:calibre:)?series_index[\'"][^>]+content=[\'"]([^\'"]+)[\'"]|content=[\'"]([^\'"]+)[\'"][^>]+name=[\'"](?:calibre:)?series_index[\'"])',
            raw, re.IGNORECASE
        )
        if m:
            val_bytes = m.group(1) or m.group(2)
            val_str = val_bytes.decode("ascii", errors="ignore").strip()
            return float(val_str)

        # EPUB3 belongs-to-collection group-position
        m_pos = re.search(
            rb'<meta[^>]+property=[\'"]group-position[\'"][^>]*>([^<]+)</meta>',
            raw, re.IGNORECASE
        )
        if m_pos:
            return float(m_pos.group(1).decode("ascii", errors="ignore").strip())
    except Exception:
        pass
    return None


def extract_series_index_from_epub(epub_path: Path) -> Optional[float]:
    """Extract series_index from inside an EPUB archive."""
    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            container_data = zf.read("META-INF/container.xml")
            m_opf = re.search(rb'full-path=[\'"]([^\'"]+)[\'"]', container_data)
            if not m_opf:
                return None
            opf_path = m_opf.group(1).decode("utf-8", errors="ignore")
            if opf_path not in zf.namelist():
                return None
            opf_bytes = zf.read(opf_path)

            m = re.search(
                rb'<meta[^>]+(?:name=[\'"](?:calibre:)?series_index[\'"][^>]+content=[\'"]([^\'"]+)[\'"]|content=[\'"]([^\'"]+)[\'"][^>]+name=[\'"](?:calibre:)?series_index[\'"])',
                opf_bytes, re.IGNORECASE
            )
            if m:
                val = (m.group(1) or m.group(2)).decode("ascii", errors="ignore").strip()
                return float(val)

            m_pos = re.search(
                rb'<meta[^>]+property=[\'"]group-position[\'"][^>]*>([^<]+)</meta>',
                opf_bytes, re.IGNORECASE
            )
            if m_pos:
                return float(m_pos.group(1).decode("ascii", errors="ignore").strip())
    except Exception:
        pass
    return None


def extract_series_index_from_fb2(fb2_path: Path) -> Optional[float]:
    """Extract sequence number from an FB2 XML file."""
    try:
        with open(fb2_path, "rb") as f:
            head = f.read(65536)
        m = re.search(rb'<sequence[^>]+number=[\'"]([^\'"]+)[\'"]', head, re.IGNORECASE)
        if m:
            return float(m.group(1).decode("ascii", errors="ignore").strip())
    except Exception:
        pass
    return None


def resolve_book_dir(lib_root: Path, rel_path: str) -> Optional[Path]:
    """Find the book directory, checking possible library roots."""
    direct = lib_root / rel_path
    if direct.is_dir():
        return direct
    # Fallback to common library locations if lib_root differs
    candidates = [
        Path("/data/books/EBLib2") / rel_path,
        Path("/data/books/EBLib") / rel_path,
        Path("/data/books") / rel_path,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def worker_task(batch: List[Tuple[int, str, float, str]]) -> List[Tuple[int, float]]:
    """
    Worker task: (book_id, rel_path, current_index, lib_root_str)
    Returns list of (book_id, new_series_index).
    """
    updates = []
    lib_root = Path(batch[0][3]) if batch else Path("/data/books")

    for book_id, rel_path, current_idx, _ in batch:
        book_dir = resolve_book_dir(lib_root, rel_path)
        if not book_dir:
            continue

        found_idx = None

        # 1. Try metadata.opf
        opf_file = book_dir / "metadata.opf"
        if opf_file.is_file():
            found_idx = extract_series_index_from_opf(opf_file)

        # 2. Try EPUB files if not found
        if found_idx is None:
            for item in book_dir.iterdir():
                if item.is_file() and item.suffix.lower() == ".epub":
                    found_idx = extract_series_index_from_epub(item)
                    if found_idx is not None:
                        break

        # 3. Try FB2 files if not found
        if found_idx is None:
            for item in book_dir.iterdir():
                if item.is_file() and item.suffix.lower() == ".fb2":
                    found_idx = extract_series_index_from_fb2(item)
                    if found_idx is not None:
                        break

        if found_idx is not None and (current_idx is None or abs(found_idx - current_idx) > 0.001):
            updates.append((book_id, found_idx))

    return updates


def main():
    parser = argparse.ArgumentParser(description="Repair series volume index for library books")
    parser.add_argument("--all", action="store_true", help="Scan all books in series, not just those with index 1.0 or NULL")
    parser.add_argument("--workers", type=int, default=max(4, os.cpu_count() or 4), help="Worker processes")
    parser.add_argument("--batch-size", type=int, default=1000, help="DB commit batch size")
    parser.add_argument("--dry-run", action="store_true", help="Preview updates without modifying database")
    args = parser.parse_args()

    lib_root = Path(settings.LIBRARY_ROOT)
    print("=" * 76)
    print(" 📚 ATHENEA SERIES INDEX REPAIR")
    print(f" Library Root: {lib_root}")
    print(f" Workers:      {args.workers}")
    print(f" Mode:         {'DRY RUN (No changes)' if args.dry_run else 'LIVE UPDATE'}")
    print("=" * 76)

    conn = get_db_connection()
    cur = conn.cursor()

    print("[*] Querying books with series links...")
    if args.all:
        cur.execute("""
            SELECT b.id, b.path, b.series_index
            FROM books b
            JOIN book_series bs ON b.id = bs.book_id
            WHERE b.path IS NOT NULL
            ORDER BY b.id
        """)
    else:
        cur.execute("""
            SELECT b.id, b.path, b.series_index
            FROM books b
            JOIN book_series bs ON b.id = bs.book_id
            WHERE b.path IS NOT NULL
              AND (b.series_index IS NULL OR b.series_index = 1.0)
            ORDER BY b.id
        """)

    rows = cur.fetchall()
    total_books = len(rows)
    print(f"[✓] Found {total_books:,} books to inspect.")

    if total_books == 0:
        print("[✓] All books already have proper series indexes! Nothing to do.")
        conn.close()
        return

    chunk_size = 200
    chunks = []
    lib_root_str = str(lib_root)
    for i in range(0, total_books, chunk_size):
        chunk = [(r["id"], r["path"], float(r["series_index"]) if r["series_index"] is not None else None, lib_root_str) for r in rows[i:i + chunk_size]]
        chunks.append(chunk)

    print(f"[*] Scanning {len(chunks):,} task chunks across {args.workers} workers...\n")

    start_time = time.time()
    books_scanned = 0
    updated_count = 0
    pending_updates: List[Tuple[float, int]] = []

    def flush_updates():
        nonlocal pending_updates, updated_count
        if not pending_updates:
            return
        if not args.dry_run:
            execute_batch(cur, """
                UPDATE books SET series_index = %s WHERE id = %s
            """, [(idx, bid) for bid, idx in pending_updates], page_size=1000)
            conn.commit()
        updated_count += len(pending_updates)
        pending_updates = []

    with mp.Pool(processes=args.workers) as pool:
        for chunk_results in pool.imap_unordered(worker_task, chunks, chunksize=1):
            books_scanned += chunk_size
            if books_scanned > total_books:
                books_scanned = total_books

            for book_id, found_idx in chunk_results:
                pending_updates.append((book_id, found_idx))

            if len(pending_updates) >= args.batch_size:
                flush_updates()

            elapsed = time.time() - start_time
            rate = books_scanned / elapsed if elapsed > 0 else 0
            sys.stdout.write(
                f"\r\033[K[Scanned: {books_scanned:,}/{total_books:,}] "
                f"Repaired: {updated_count + len(pending_updates):,} ({rate:,.0f} books/sec)"
            )
            sys.stdout.flush()

    flush_updates()
    total_time = time.time() - start_time

    print(f"\n\n[✓] Done in {total_time:.1f}s.")
    print(f"[✓] Total series indexes repaired: {updated_count:,}")
    if args.dry_run:
        print("    (Dry run completed: no database rows were modified)")
    conn.close()


if __name__ == "__main__":
    main()
