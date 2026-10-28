import os
import sys
import time
from pathlib import Path
import psycopg2
from psycopg2.extras import DictCursor

# DB connection
conn = psycopg2.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", 5432)),
    dbname=os.getenv("DB_NAME", "booksdb"),
    user=os.getenv("DB_USER", "books"),
    password=os.getenv("DB_PASSWORD", ""),
    cursor_factory=DictCursor
)

print("[*] Loading all book paths from PostgreSQL...")
t0 = time.time()
cur = conn.cursor()
cur.execute("SELECT path FROM books")
db_paths = set()
for row in cur.fetchall():
    if row[0]:
        db_paths.add(row[0].strip().replace('\\', '/'))
cur.close()
conn.close()
t1 = time.time()
print(f"[✓] Loaded {len(db_paths):,} book paths from Postgres in {t1 - t0:.2f}s")

library_root = Path("/mnt/mainlib/Books/EBLib")
print(f"[*] Scanning library at {library_root}...")

scanned_dirs = 0
books_on_disk = 0
missing_books = []

t2 = time.time()
with os.scandir(library_root) as author_it:
    for author_entry in author_it:
        if not author_entry.is_dir(follow_symlinks=False):
            continue
        scanned_dirs += 1
        
        try:
            with os.scandir(author_entry.path) as book_it:
                for book_entry in book_it:
                    if not book_entry.is_dir(follow_symlinks=False):
                        continue
                    books_on_disk += 1
                    rel_path = f"{author_entry.name}/{book_entry.name}".replace('\\', '/')
                    
                    if rel_path not in db_paths:
                        missing_books.append((rel_path, book_entry.path))
                        if len(missing_books) <= 5:
                            print(f"[!] Found missing book: {rel_path}")
        except OSError:
            continue

        if scanned_dirs % 20000 == 0:
            print(f"  Scanned {scanned_dirs:,} author folders... ({books_on_disk:,} book folders, {len(missing_books)} missing so far)")

t3 = time.time()
print("\n" + "=" * 60)
print(f"Scan complete in {t3 - t2:.2f}s!")
print(f"Author folders:  {scanned_dirs:,}")
print(f"Books on disk:   {books_on_disk:,}")
print(f"Books in DB:     {len(db_paths):,}")
print(f"Missing from DB: {len(missing_books):,}")
print("=" * 60)
