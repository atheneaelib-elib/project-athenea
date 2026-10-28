#!/usr/bin/env python3
"""
EBook Library Database Deduplication Tool
Identifies and cleans up duplicate book records in PostgreSQL booksdb.

Deduplication Strategy:
1. Exact Path Duplicates:
   - When multiple book records share the exact same filesystem path.
   - Retains the lowest (original) ID.
   - Merges formats, identifiers, covers, metadata (pubdate, publisher, series, tags,
     languages, description), and user activity (favorites, progress, bookmarks, notes)
     into the primary record.
   - Deletes redundant duplicate records from books and all related tables.

2. Exact Title + Author Duplicates:
   - When multiple records share the same normalized title and author_sort.
   - Selects the best primary record (has cover, has formats, lowest ID).
   - Merges missing formats, metadata, and user activity into the primary record.
   - Deletes redundant duplicate records.

3. Creates PostgreSQL UNIQUE INDEX on books(path) and data(book_id, format) to permanently prevent duplicates.
"""

import sys
import argparse
from pathlib import Path
from typing import List

import psycopg2
from psycopg2.extras import DictCursor

# Ensure project root (/opt/athenea) can be imported
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


def merge_records_into_primary(cur, primary_id: int, redundant_ids: List[int]):
    """Merge metadata, formats, and user records from redundant_ids into primary_id."""
    if not redundant_ids:
        return

    for dup_id in redundant_ids:
        # 1. Backfill missing pubdate, isbn, has_cover on primary book row
        cur.execute("""
            UPDATE books p
            SET pubdate = COALESCE(p.pubdate, d.pubdate),
                isbn = COALESCE(NULLIF(p.isbn, ''), NULLIF(d.isbn, '')),
                has_cover = GREATEST(COALESCE(p.has_cover, 0), COALESCE(d.has_cover, 0))
            FROM books d
            WHERE p.id = %s AND d.id = %s
        """, (primary_id, dup_id))

        # 2. Merge formats (data table)
        cur.execute("SELECT format, uncompressed_size, name FROM data WHERE book_id = %s", (dup_id,))
        for d_row in cur.fetchall():
            cur.execute("""
                SELECT 1 FROM data WHERE book_id = %s AND UPPER(format) = UPPER(%s)
            """, (primary_id, d_row["format"]))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO data (book_id, format, uncompressed_size, name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (primary_id, d_row["format"], d_row["uncompressed_size"], d_row["name"]))

        # 3. Merge identifiers
        cur.execute("SELECT type, val FROM identifiers WHERE book_id = %s", (dup_id,))
        for i_row in cur.fetchall():
            cur.execute("""
                INSERT INTO identifiers (book_id, type, val)
                VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
            """, (primary_id, i_row["type"], i_row["val"]))

        # 4. Merge authors, publishers, series, tags, languages
        for tbl, col in [
            ("book_authors", "author_id"),
            ("book_publishers", "publisher_id"),
            ("book_series", "series_id"),
            ("book_tags", "tag_id"),
            ("book_languages", "language_id"),
        ]:
            cur.execute(f"""
                INSERT INTO {tbl} (book_id, {col})
                SELECT %s, {col} FROM {tbl} WHERE book_id = %s
                ON CONFLICT DO NOTHING
            """, (primary_id, dup_id))

        # 5. Merge comments (description) if primary has none
        cur.execute("SELECT text FROM comments WHERE book_id = %s", (primary_id,))
        c_row = cur.fetchone()
        if not c_row or not c_row[0]:
            cur.execute("SELECT text FROM comments WHERE book_id = %s", (dup_id,))
            dup_c = cur.fetchone()
            if dup_c and dup_c[0]:
                cur.execute("""
                    INSERT INTO comments (book_id, text)
                    VALUES (%s, %s)
                    ON CONFLICT (book_id) DO UPDATE SET text = EXCLUDED.text
                """, (primary_id, dup_c[0]))

        # 6. Preserve user favorites, bookmarks, notes, reading progress & status
        for u_tbl in ("favorites", "reading_progress", "reading_status"):
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (u_tbl,))
            if cur.fetchone():
                cur.execute(f"""
                    UPDATE {u_tbl} SET book_id = %s
                    WHERE book_id = %s
                      AND user_id NOT IN (SELECT user_id FROM {u_tbl} WHERE book_id = %s)
                """, (primary_id, dup_id, primary_id))

        for note_tbl in ("user_bookmarks", "user_notes"):
            cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (note_tbl,))
            if cur.fetchone():
                cur.execute(f"UPDATE {note_tbl} SET book_id = %s WHERE book_id = %s", (primary_id, dup_id))


def delete_book_records(cur, book_ids: List[int]):
    """Safely delete books and all their associated rows across all tables."""
    if not book_ids:
        return
    tup = tuple(book_ids)
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
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name = %s", (tbl,))
        if cur.fetchone():
            cur.execute(f"DELETE FROM {tbl} WHERE book_id IN %s", (tup,))
    cur.execute("DELETE FROM books WHERE id IN %s", (tup,))


def merge_and_deduplicate(conn, dry_run: bool = False, merge_title_author: bool = True, keep_all_languages: bool = True):
    print("=" * 76)
    print(" 🧹 EBOOK LIBRARY DATABASE DEDUPLICATION")
    print(f" Database: {settings.DB_NAME} on {settings.DB_HOST}")
    print(f" Mode:     {'DRY RUN (No changes)' if dry_run else 'LIVE CLEANUP (Modifying DB)'}")
    print(f" Languages:{'KEEP ALL (preserve different language editions)' if keep_all_languages else 'MERGE ALL (merge same title & author across languages)'}")
    print("=" * 76)

    with conn.cursor() as cur:
        # Phase 1: Exact Path Duplicates
        print("\n[*] Phase 1: Scanning for exact duplicate paths...")
        cur.execute("""
            SELECT path, array_agg(id ORDER BY id ASC) as ids
            FROM books
            WHERE path IS NOT NULL AND path != ''
            GROUP BY path
            HAVING count(*) > 1
        """)
        path_dups = cur.fetchall()
        print(f"[+] Found {len(path_dups):,} duplicate path groups.")

        path_removed_count = 0
        for row in path_dups:
            path = row["path"]
            ids = row["ids"]
            primary_id = ids[0]
            redundant_ids = ids[1:]
            path_removed_count += len(redundant_ids)

            if dry_run:
                print(f"  [DRY-RUN] Path: {path} -> Keep #{primary_id}, Remove: {redundant_ids}")
            else:
                merge_records_into_primary(cur, primary_id, redundant_ids)
                delete_book_records(cur, redundant_ids)

        if not dry_run:
            conn.commit()
            print(f"[✓] Phase 1 complete: Removed {path_removed_count:,} duplicate path records.")
        else:
            print(f"[✓] Phase 1 dry run: Would remove {path_removed_count:,} duplicate path records.")

        # Phase 2: Title + Author Duplicates
        title_author_removed = 0
        if merge_title_author:
            print("\n[*] Phase 2: Scanning for duplicate (title, author) records...")
            if keep_all_languages:
                cur.execute("""
                    SELECT 
                        LOWER(TRIM(b.title)) as norm_title,
                        LOWER(TRIM(b.author_sort)) as norm_author,
                        COALESCE(l.lang_code, '') as norm_lang,
                        array_agg(b.id ORDER BY b.has_cover DESC, b.id ASC) as ids
                    FROM books b
                    LEFT JOIN book_languages bl ON bl.book_id = b.id
                    LEFT JOIN languages l ON l.id = bl.language_id
                    WHERE b.title IS NOT NULL AND b.author_sort IS NOT NULL AND b.path IS NOT NULL AND b.path != ''
                    GROUP BY LOWER(TRIM(b.title)), LOWER(TRIM(b.author_sort)), COALESCE(l.lang_code, '')
                    HAVING count(*) > 1
                """)
            else:
                cur.execute("""
                    SELECT 
                        LOWER(TRIM(title)) as norm_title,
                        LOWER(TRIM(author_sort)) as norm_author,
                        '' as norm_lang,
                        array_agg(id ORDER BY has_cover DESC, id ASC) as ids
                    FROM books
                    WHERE title IS NOT NULL AND author_sort IS NOT NULL AND path IS NOT NULL AND path != ''
                    GROUP BY LOWER(TRIM(title)), LOWER(TRIM(author_sort))
                    HAVING count(*) > 1
                """)
            title_dups = cur.fetchall()
            print(f"[+] Found {len(title_dups):,} duplicate (title, author) groups.")

            for row in title_dups:
                ids = row["ids"]
                primary_id = ids[0]
                redundant_ids = ids[1:]
                title_author_removed += len(redundant_ids)

                if dry_run:
                    if title_author_removed <= 15:
                        lang_info = f" [{row['norm_lang'].upper()}]" if row['norm_lang'] else ""
                        print(f"  [DRY-RUN] Title/Author{lang_info}: '{row['norm_title']}' by '{row['norm_author']}' -> Keep #{primary_id}, Remove: {redundant_ids}")
                else:
                    merge_records_into_primary(cur, primary_id, redundant_ids)
                    delete_book_records(cur, redundant_ids)

            if not dry_run:
                conn.commit()
                print(f"[✓] Phase 2 complete: Removed {title_author_removed:,} duplicate (title, author) records.")
            else:
                print(f"[✓] Phase 2 dry run: Would remove {title_author_removed:,} duplicate (title, author) records.")

        # Phase 3: Enforce Unique Path & Format Constraints in PostgreSQL
        if not dry_run:
            print("\n[*] Phase 3: Creating UNIQUE INDEXES on books(path) and data(book_id, format)...")
            try:
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_books_unique_path 
                    ON books (path) 
                    WHERE path IS NOT NULL AND path != '';
                """)
                cur.execute("""
                    DELETE FROM data a
                    USING data b
                    WHERE a.id > b.id
                      AND a.book_id = b.book_id
                      AND UPPER(a.format) = UPPER(b.format);
                """)
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_data_book_format
                    ON data (book_id, format);
                """)
                conn.commit()
                print("[✓] PostgreSQL UNIQUE INDEXES ('idx_books_unique_path', 'idx_data_book_format') active and enforced.")
            except Exception as e:
                print(f"[!] Warning creating unique index: {e}")
                conn.rollback()

    print("\n" + "=" * 76)
    print(" 🏁 DEDUPLICATION FINISHED")
    print(f" Total Duplicates Removed: {path_removed_count + title_author_removed:,}")
    print("=" * 76)


def main():
    parser = argparse.ArgumentParser(description="Deduplicate EBook Library Database.")
    parser.add_argument("--dry-run", action="store_true", help="Audit duplicates without modifying the database")
    parser.add_argument("--paths-only", action="store_true", help="Only deduplicate exact same path records")
    parser.add_argument("--keep-all-languages", action="store_true", default=True, help="Preserve different language editions of books with the same title & author (default: True)")
    parser.add_argument("--no-keep-all-languages", action="store_false", dest="keep_all_languages", help="Merge books with same title & author across different languages")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        merge_and_deduplicate(
            conn,
            dry_run=args.dry_run,
            merge_title_author=not args.paths_only,
            keep_all_languages=args.keep_all_languages
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
