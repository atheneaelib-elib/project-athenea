#!/usr/bin/env python3
"""
High-Speed Series Backfill for Athenea
======================================
Scans all books currently missing series links in PostgreSQL,
inspects their EPUB/FB2 files in parallel, extracts Calibre/EPUB3 series
and series index metadata, and links them in PostgreSQL.

Usage:
    /opt/athenea/venv/bin/python -m cli.backfill_series
    /opt/athenea/venv/bin/python -m cli.backfill_series --workers 8 --batch-size 1000
"""

import sys
import os
import re
import time
import argparse
import zipfile
import html
import multiprocessing as mp
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import xml.etree.ElementTree as ET

import psycopg2
from psycopg2.extras import DictCursor, execute_batch

# Ensure app root is in sys.path
APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from settings import settings


def get_db_connection():
    """Create a psycopg2 connection with UTF-8 encoding."""
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


def extract_series_from_epub(epub_path: Path) -> Tuple[Optional[str], float]:
    """Blazing-fast extraction of series and series_index from an EPUB container."""
    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            # 1. Locate rootfile from container.xml
            container_data = zf.read("META-INF/container.xml")
            m_opf = re.search(rb'full-path=[\'"]([^\'"]+)[\'"]', container_data)
            if not m_opf:
                return None, 1.0
            opf_path = m_opf.group(1).decode("utf-8", errors="ignore")
            if opf_path not in zf.namelist():
                return None, 1.0

            opf_bytes = zf.read(opf_path)

            series_name = None
            series_index = 1.0

            # Fast regex scans for Calibre series
            # Pattern A: name="calibre:series" content="..."
            m_s = re.search(
                rb'<meta[^>]+(?:name=[\'"](?:calibre:)?series[\'"][^>]+content=[\'"]([^\'"]+)[\'"]|content=[\'"]([^\'"]+)[\'"][^>]+name=[\'"](?:calibre:)?series[\'"])',
                opf_bytes, re.IGNORECASE
            )
            if m_s:
                raw_s = m_s.group(1) or m_s.group(2)
                series_name = html.unescape(raw_s.decode("utf-8", errors="replace")).strip()

            # Pattern B: name="calibre:series_index" content="..."
            m_idx = re.search(
                rb'<meta[^>]+(?:name=[\'"](?:calibre:)?series_index[\'"][^>]+content=[\'"]([^\'"]+)[\'"]|content=[\'"]([^\'"]+)[\'"][^>]+name=[\'"](?:calibre:)?series_index[\'"])',
                opf_bytes, re.IGNORECASE
            )
            if m_idx:
                raw_idx = m_idx.group(1) or m_idx.group(2)
                try:
                    series_index = float(raw_idx.decode("ascii", errors="ignore").strip())
                except ValueError:
                    pass

            # Pattern C: EPUB3 belongs-to-collection
            if not series_name:
                m_coll = re.search(
                    rb'<meta[^>]+property=[\'"]belongs-to-collection[\'"][^>]*>([^<]+)</meta>',
                    opf_bytes, re.IGNORECASE
                )
                if m_coll:
                    series_name = html.unescape(m_coll.group(1).decode("utf-8", errors="replace")).strip()

                m_pos = re.search(
                    rb'<meta[^>]+property=[\'"]group-position[\'"][^>]*>([^<]+)</meta>',
                    opf_bytes, re.IGNORECASE
                )
                if m_pos:
                    try:
                        series_index = float(m_pos.group(1).decode("ascii", errors="ignore").strip())
                    except ValueError:
                        pass

            # Fallback to XML parsing if regex didn't catch it
            if not series_name:
                try:
                    clean_text = re.sub(r'(</?)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)', r'\1\2', opf_bytes.decode("utf-8", errors="ignore"))
                    clean_text = re.sub(r'(\s)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)=', r'\1\2=', clean_text)
                    root = ET.fromstring(clean_text)
                    for elem in root.iter():
                        tag = elem.tag.split("}")[-1].lower()
                        if tag == "meta":
                            name = elem.attrib.get("name", "").lower()
                            prop = elem.attrib.get("property", "").lower()
                            cnt = elem.attrib.get("content", "").strip()
                            txt = elem.text.strip() if elem.text else ""
                            if name.endswith("series") and (cnt or txt):
                                series_name = cnt or txt
                            elif name.endswith("series_index") and (cnt or txt):
                                try:
                                    series_index = float(cnt or txt)
                                except ValueError:
                                    pass
                            elif prop == "belongs-to-collection" and txt:
                                series_name = txt
                            elif prop == "group-position" and txt:
                                try:
                                    series_index = float(txt)
                                except ValueError:
                                    pass
                except Exception:
                    pass

            if series_name:
                return series_name, series_index
    except Exception:
        pass

    return None, 1.0


def worker_task(batch: List[Tuple[int, str, str]]) -> List[Tuple[int, str, float]]:
    """Worker function processing a chunk of books: (book_id, rel_path, lib_root)."""
    results = []
    for book_id, rel_path, lib_root_str in batch:
        lib_root = Path(lib_root_str)
        book_dir = lib_root / rel_path
        if not book_dir.is_dir():
            continue

        series_name = None
        series_index = 1.0

        # Check metadata.opf first
        opf_path = book_dir / "metadata.opf"
        if opf_path.is_file():
            try:
                raw = opf_path.read_bytes()
                m_s = re.search(
                    rb'<meta[^>]+(?:name=[\'"](?:calibre:)?series[\'"][^>]+content=[\'"]([^\'"]+)[\'"]|content=[\'"]([^\'"]+)[\'"][^>]+name=[\'"](?:calibre:)?series[\'"])',
                    raw, re.IGNORECASE
                )
                if m_s:
                    raw_s = m_s.group(1) or m_s.group(2)
                    series_name = html.unescape(raw_s.decode("utf-8", errors="replace")).strip()
                m_idx = re.search(
                    rb'<meta[^>]+(?:name=[\'"](?:calibre:)?series_index[\'"][^>]+content=[\'"]([^\'"]+)[\'"]|content=[\'"]([^\'"]+)[\'"][^>]+name=[\'"](?:calibre:)?series_index[\'"])',
                    raw, re.IGNORECASE
                )
                if m_idx:
                    raw_idx = m_idx.group(1) or m_idx.group(2)
                    try:
                        series_index = float(raw_idx.decode("ascii", errors="ignore").strip())
                    except ValueError:
                        pass
            except Exception:
                pass

        # If not found in metadata.opf, scan EPUB files
        if not series_name:
            try:
                for item in book_dir.iterdir():
                    if item.is_file() and item.suffix.lower() == ".epub":
                        s_name, s_idx = extract_series_from_epub(item)
                        if s_name:
                            series_name = s_name
                            series_index = s_idx
                            break
            except Exception:
                pass

        if series_name:
            results.append((book_id, series_name, series_index))

    return results


def main():
    parser = argparse.ArgumentParser(description="Backfill missing series for library books")
    parser.add_argument("--workers", type=int, default=max(4, os.cpu_count() or 4), help="Number of parallel worker processes")
    parser.add_argument("--batch-size", type=int, default=1000, help="DB commit batch size")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of books to process (0 = all)")
    args = parser.parse_args()

    lib_root = Path(settings.LIBRARY_ROOT)
    if not lib_root.is_dir():
        print(f"[!] Error: Library root {lib_root} does not exist.")
        sys.exit(1)

    print("=" * 76)
    print(" 📚 ATHENEA FAST SERIES BACKFILL")
    print(f" Library Root: {lib_root}")
    print(f" Workers:      {args.workers}")
    print("=" * 76)

    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Load existing series cache from PostgreSQL
    print("[*] Pre-caching existing series from database...")
    cur.execute("SELECT id, LOWER(name) FROM series")
    series_cache: Dict[str, int] = {row[1]: row[0] for row in cur.fetchall()}
    print(f"[✓] Cached {len(series_cache):,} series.")

    # 2. Query books that currently lack a book_series entry
    print("[*] Querying books missing series links...")
    sql = """
        SELECT b.id, b.path
        FROM books b
        WHERE b.path IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM book_series bs WHERE bs.book_id = b.id
          )
        ORDER BY b.id
    """
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"

    cur.execute(sql)
    rows = cur.fetchall()
    total_books = len(rows)
    print(f"[✓] Found {total_books:,} books missing series records.")

    if total_books == 0:
        print("[✓] All books already have series data linked! Nothing to do.")
        conn.close()
        return

    # 3. Chunk rows for multiprocessing
    chunk_size = 250
    chunks = []
    lib_root_str = str(lib_root)
    for i in range(0, total_books, chunk_size):
        chunk = [(r["id"], r["path"], lib_root_str) for r in rows[i:i + chunk_size]]
        chunks.append(chunk)

    print(f"[*] Dispatching {len(chunks):,} task chunks across {args.workers} workers...\n")

    start_time = time.time()
    books_scanned = 0
    series_linked = 0
    pending_updates: List[Tuple[float, int]] = []
    pending_links: List[Tuple[int, int]] = []

    def flush_batch():
        nonlocal pending_updates, pending_links
        if not pending_links and not pending_updates:
            return
        # Bulk insert into book_series
        if pending_links:
            execute_batch(cur, """
                INSERT INTO book_series (book_id, series_id)
                VALUES (%s, %s)
                ON CONFLICT (book_id, series_id) DO NOTHING
            """, pending_links, page_size=1000)
        # Bulk update books.series_index
        if pending_updates:
            execute_batch(cur, """
                UPDATE books SET series_index = %s WHERE id = %s
            """, pending_updates, page_size=1000)
        conn.commit()
        pending_links = []
        pending_updates = []

    with mp.Pool(processes=args.workers) as pool:
        for chunk_results in pool.imap_unordered(worker_task, chunks, chunksize=1):
            books_scanned += chunk_size
            if books_scanned > total_books:
                books_scanned = total_books

            for book_id, series_name, series_index in chunk_results:
                clean_name = series_name.strip()
                if not clean_name:
                    continue
                lower_name = clean_name.lower()
                sid = series_cache.get(lower_name)
                if not sid:
                    # Create new series record
                    cur.execute("INSERT INTO series (name) VALUES (%s) RETURNING id", (clean_name,))
                    sid = cur.fetchone()[0]
                    series_cache[lower_name] = sid

                pending_links.append((book_id, sid))
                if series_index and series_index != 1.0:
                    pending_updates.append((series_index, book_id))
                series_linked += 1

            if len(pending_links) >= args.batch_size:
                flush_batch()

            elapsed = time.time() - start_time
            rate = books_scanned / elapsed if elapsed > 0 else 0
            sys.stdout.write(
                f"\r\033[K[Scanned: {books_scanned:,}/{total_books:,}] "
                f"| 📚 Series Linked: {series_linked:,} "
                f"| ⚡ {rate:.0f} books/s "
                f"| Elapsed: {int(elapsed)}s"
            )
            sys.stdout.flush()

    # Final commit
    flush_batch()
    conn.close()

    total_time = time.time() - start_time
    print("\n\n" + "=" * 76)
    print(" 🏁 SERIES BACKFILL COMPLETE")
    print(f" Total Books Scanned: {total_books:,}")
    print(f" Series Links Created: {series_linked:,}")
    print(f" Unique Series in DB: {len(series_cache):,}")
    print(f" Total Time:          {total_time:.1f}s ({total_books / total_time:.0f} books/s)")
    print("=" * 76)


if __name__ == "__main__":
    main()
