#!/usr/bin/env python3
"""
EBook Library Author Deduplication & Canonicalization Tool
=========================================================
Identifies and merges duplicate author records in PostgreSQL (Athenea):
1. Word-order variations:
   e.g. "Панов Вадим Юрьевич" == "Панов, Вадим Юрьевич" == "Вадим Юрьевич Панов"
2. Missing middle-name / patronymic completion:
   e.g. "Вадим Панов" -> "Вадим Юрьевич Панов" (when unambiguous or confirmed by shared series)
3. Safety rules:
   - Conflicting middle names are NEVER merged (e.g. "Алексей Николаевич Толстой" != "Алексей Константинович Толстой").
   - Ambiguous short names with multiple candidate full names are skipped unless confirmed by a shared series.
   - Single-word names (e.g. "Иванов", "Smith") are never merged.
"""

import sys
import re
import argparse
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Set, Tuple, Optional

import psycopg2
from psycopg2.extras import DictCursor

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


def extract_author_words(name: str) -> List[str]:
    """Extract clean words from an author name."""
    clean = (name or "").strip().lower().replace("ё", "е")
    # Remove commas, dots, quotes, brackets
    words = [w for w in re.findall(r'[a-zA-Zа-яА-Я0-9]+', clean) if len(w) >= 2]
    return words


def get_name_tokens(name: str) -> Tuple[str, ...]:
    """Return sorted unique tokens of author name."""
    words = extract_author_words(name)
    return tuple(sorted(words))


def format_canonical_name(name: str) -> str:
    """Format name into clean 'First Middle Last' order without stray commas or trailing spaces."""
    s = (name or "").strip()
    s = re.sub(r'\s+', ' ', s)
    if ',' in s:
        # e.g. "Панов, Вадим Юрьевич" -> "Вадим Юрьевич Панов"
        parts = [p.strip() for p in s.split(',', 1)]
        if len(parts) == 2 and parts[1]:
            s = f"{parts[1]} {parts[0]}"
    return s


def merge_author_records(cur, canonical_id: int, canonical_name: str, redundant_ids: List[int]):
    """Merge redundant author IDs into canonical_id across all relevant tables."""
    if not redundant_ids:
        return

    for dup_id in redundant_ids:
        # 1. Update book_authors avoiding duplicates
        cur.execute("""
            UPDATE book_authors ba
            SET author_id = %s
            WHERE ba.author_id = %s
              AND ba.book_id NOT IN (
                  SELECT book_id FROM book_authors WHERE author_id = %s
              );
        """, (canonical_id, dup_id, canonical_id))

        # Remove any leftover duplicates in book_authors for dup_id
        cur.execute("DELETE FROM book_authors WHERE author_id = %s;", (dup_id,))

        # 2. Delete redundant author row
        cur.execute("DELETE FROM authors WHERE id = %s;", (dup_id,))

    # 3. Update author_sort display on affected books
    cur.execute("""
        UPDATE books b
        SET author_sort = %s
        FROM book_authors ba
        WHERE b.id = ba.book_id
          AND ba.author_id = %s;
    """, (canonical_name, canonical_id))


def deduplicate_authors(conn, dry_run: bool = True, min_books: int = 0):
    print("=" * 76)
    print(" 👤 ATHENEA AUTHOR DEDUPLICATION & CANONICALIZATION")
    print(f" Database: {settings.DB_NAME} on {settings.DB_HOST}")
    print(f" Mode:     {'DRY RUN (Preview only)' if dry_run else 'LIVE MERGE (Updating DB)'}")
    print("=" * 76)

    with conn.cursor() as cur:
        # 1. Retrieve all authors with their book counts
        print("[*] Loading authors and book counts from database...")
        cur.execute("""
            SELECT 
                a.id,
                a.name,
                count(ba.book_id) as book_count
            FROM authors a
            LEFT JOIN book_authors ba ON a.id = ba.author_id
            WHERE a.name IS NOT NULL AND TRIM(a.name) != ''
            GROUP BY a.id, a.name
            ORDER BY count(ba.book_id) DESC, a.id ASC;
        """)
        all_authors = cur.fetchall()
        print(f"[✓] Loaded {len(all_authors):,} authors.")

        # 2. Load series associated with each author for disambiguation
        print("[*] Mapping authors to series for safe disambiguation...")
        cur.execute("""
            SELECT DISTINCT ba.author_id, bs.series_id
            FROM book_authors ba
            JOIN book_series bs ON ba.book_id = bs.book_id;
        """)
        author_series_map: Dict[int, Set[int]] = defaultdict(set)
        for row in cur.fetchall():
            author_series_map[row["author_id"]].add(row["series_id"])

        # -------------------------------------------------------------
        # Phase 1: Exact Word-Order Duplicates
        # e.g. "Панов Вадим" == "Вадим Панов" == "Панов, Вадим"
        # -------------------------------------------------------------
        print("\n[*] Phase 1: Identifying exact word-order duplicates...")
        token_groups: Dict[Tuple[str, ...], List[dict]] = defaultdict(list)
        for a in all_authors:
            tokens = get_name_tokens(a["name"])
            if len(tokens) >= 2:
                token_groups[tokens].append(a)

        exact_merge_groups = []
        for tokens, group in token_groups.items():
            if len(group) > 1:
                # Pick primary: highest book count, then clean formatting, lowest ID
                sorted_group = sorted(
                    group,
                    key=lambda x: (x["book_count"], not (',' in x["name"]), -x["id"]),
                    reverse=True
                )
                canonical = sorted_group[0]
                canonical_formatted = format_canonical_name(canonical["name"])
                redundant = sorted_group[1:]
                exact_merge_groups.append((canonical, canonical_formatted, redundant))

        print(f"[+] Found {len(exact_merge_groups):,} exact word-order author duplicate groups.")

        # -------------------------------------------------------------
        # Phase 2: Missing Middle-Name Expansion (First Last -> First Middle Last)
        # -------------------------------------------------------------
        print("\n[*] Phase 2: Analyzing 2-word authors with matching 3-word candidates...")

        # Map {First, Last} pairs to all 3-word author records
        fl_to_full_authors: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
        two_word_authors: List[dict] = []

        for a in all_authors:
            words = extract_author_words(a["name"])
            if len(words) == 3:
                # Potential First Middle Last or Last First Middle
                # Generate sorted (First, Last) pairs assuming middle is patronymic/middle name
                w1, w2, w3 = words
                # Case A: First Middle Last -> First, Last are (w1, w3)
                # Case B: Last First Middle -> First, Last are (w2, w1)
                fl_to_full_authors[tuple(sorted([w1, w3]))].append(a)
                fl_to_full_authors[tuple(sorted([w1, w2]))].append(a)
            elif len(words) == 2:
                two_word_authors.append(a)

        middle_name_merge_groups = []
        ambiguous_skips = []

        for short_author in two_word_authors:
            words = extract_author_words(short_author["name"])
            key = tuple(sorted(words))
            candidates = fl_to_full_authors.get(key, [])

            if not candidates:
                continue

            # Deduplicate candidate objects by ID
            unique_candidates = {c["id"]: c for c in candidates}.values()

            if len(unique_candidates) == 1:
                # Exactly ONE candidate in whole database -> 100% Unambiguous!
                canonical = list(unique_candidates)[0]
                if canonical["id"] != short_author["id"]:
                    canonical_formatted = format_canonical_name(canonical["name"])
                    middle_name_merge_groups.append((canonical, canonical_formatted, [short_author], "Unambiguous (Single match)"))
            else:
                # Multiple candidates exist (e.g. 2 different middle names!)
                # Check for shared series disambiguation
                short_series = author_series_map.get(short_author["id"], set())
                matched_candidate = None
                if short_series:
                    for cand in unique_candidates:
                        cand_series = author_series_map.get(cand["id"], set())
                        if short_series.intersection(cand_series):
                            matched_candidate = cand
                            break

                if matched_candidate and matched_candidate["id"] != short_author["id"]:
                    canonical_formatted = format_canonical_name(matched_candidate["name"])
                    middle_name_merge_groups.append((matched_candidate, canonical_formatted, [short_author], "Confirmed by shared series"))
                else:
                    # Truly ambiguous -> Skip safely!
                    ambiguous_skips.append((short_author, list(unique_candidates)))

        print(f"[+] Found {len(middle_name_merge_groups):,} safe middle-name author completions.")
        print(f"[!] Skipped {len(ambiguous_skips):,} ambiguous authors with conflicting candidates.")

        # -------------------------------------------------------------
        # Preview / Execution
        # -------------------------------------------------------------
        all_merges = exact_merge_groups + [(c, c_fmt, r) for c, c_fmt, r, _ in middle_name_merge_groups]
        total_authors_to_remove = sum(len(r) for _, _, r in all_merges)

        print(f"\n[+] Total Author Consolidations Identified: {len(all_merges):,} ({total_authors_to_remove:,} redundant author records to remove)")

        # Preview samples
        if dry_run:
            print("\n" + "-" * 76)
            print(" 🔍 DRY RUN PREVIEW (Sample Mergers):")
            print("-" * 76)
            preview_count = 0
            for canonical, c_fmt, redundant in exact_merge_groups[:15]:
                preview_count += 1
                r_names = ", ".join(f"'{r['name']}' (#{r['id']}, {r['book_count']} b)" for r in redundant)
                print(f"  [Word-Order] Keep: '{c_fmt}' (#{canonical['id']}, {canonical['book_count']} b) <- Merge: {r_names}")

            for canonical, c_fmt, redundant, reason in middle_name_merge_groups[:20]:
                preview_count += 1
                r_names = ", ".join(f"'{r['name']}' (#{r['id']}, {r['book_count']} b)" for r in redundant)
                print(f"  [Middle-Name ({reason})] Keep: '{c_fmt}' (#{canonical['id']}, {canonical['book_count']} b) <- Merge: {r_names}")

            if ambiguous_skips:
                print("\n" + "-" * 76)
                print(" ⚠️  AMBIGUOUS AUTHORS (Safely Skipped from Auto-Merge):")
                print("-" * 76)
                for short_a, cands in ambiguous_skips[:10]:
                    cand_names = ", ".join(f"'{c['name']}' (#{c['id']})" for c in cands)
                    print(f"  [Skipped] '{short_a['name']}' (#{short_a['id']}, {short_a['book_count']} b) has multiple candidates: {cand_names}")

            print("\n[✓] Dry run finished. Run without --dry-run to apply merges to the database.")
            return

        # Live Execution
        print("\n[*] Applying author consolidations to database...")
        merged_count = 0
        for canonical, c_fmt, redundant in all_merges:
            redundant_ids = [r["id"] for r in redundant]
            merge_author_records(cur, canonical["id"], c_fmt, redundant_ids)
            merged_count += len(redundant_ids)
            if merged_count % 500 == 0:
                conn.commit()
                sys.stdout.write(f"\r Consolidated: {merged_count:,}/{total_authors_to_remove:,} authors...")
                sys.stdout.flush()

        conn.commit()
        print(f"\n[✓] Successfully merged {total_authors_to_remove:,} author records into canonical profiles.")


def main():
    parser = argparse.ArgumentParser(description="Deduplicate and Canonicalize Author Records in Athenea.")
    parser.add_argument("--dry-run", action="store_true", help="Audit author duplicates without modifying database")
    parser.add_argument("--min-books", type=int, default=0, help="Minimum book count filter")
    args = parser.parse_args()

    conn = get_db_connection()
    try:
        deduplicate_authors(conn, dry_run=args.dry_run, min_books=args.min_books)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
