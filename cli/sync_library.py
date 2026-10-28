#!/usr/bin/env python3
"""
High-Speed Calibre Library & Storage Synchronization Scanner
Compares physical book folders in the storage directory with PostgreSQL entries,
and automatically imports any missing books with full metadata, covers, and formats.

Key Features:
- O(1) set lookup of all PostgreSQL book paths & IDs (<0.2s pre-load).
- Instant Calibre metadata.opf parsing with full titles, authors, publishers,
  descriptions, series, tags, pubdates, and ISBN/UUID identifiers.
- Automatic enrichment & fallback to embedded ebook metadata (FB2, EPUB, etc.)
  when OPF is missing or lacks pubdate/publisher/cover.
- Auto-converts FB2-only folders to EPUB so every synced book is ready for the web reader.
- Optional --backfill-metadata mode to enrich existing DB books missing pubdate/publisher/cover.
- Discovers and registers all format files (.epub, .fb2, .pdf, .mobi, etc.) and cover.jpg.
- Batch database commits for maximum throughput.
- Full --dry-run mode for auditing without modifications.
- Preserves database consistency and sets service user (athenea or books) ownership.
"""

import os
import sys
import re
import time
import argparse
import uuid
import signal
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple

import psycopg2
from psycopg2.extras import DictCursor

# Ensure project root (/opt/athenea) can be imported
APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from settings import settings
from services import importer as importer_service
from cli.bulk_import import parse_fb2_fast, ensure_indexes_and_sequences, find_fb2c_binary, convert_fb2_to_epub_fast

ATHENEA_UID = None
ATHENEA_GID = None
try:
    import pwd, grp
    ATHENEA_UID = pwd.getpwnam("athenea").pw_uid
    ATHENEA_GID = grp.getgrnam("athenea").gr_gid
except Exception:
    pass


def fix_ownership(path: Path):
    """Ensure created files & directories are owned by athenea:athenea."""
    if ATHENEA_UID is not None and ATHENEA_GID is not None:
        try:
            os.chown(path, ATHENEA_UID, ATHENEA_GID)
        except Exception:
            pass


def get_db_connection():
    """Create a direct psycopg2 connection with UTF-8 client encoding."""
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


def parse_pubdate(val: Optional[str]) -> Optional[datetime]:
    """Parse various date strings into a valid datetime, ignoring dummy years <= 101."""
    if not val:
        return None
    val = str(val).strip()
    if not val:
        return None

    try:
        clean_iso = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        if dt.year > 101:
            return dt
    except Exception:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%B %Y",
        "%b %Y",
        "%Y",
    ):
        try:
            dt = datetime.strptime(val[:19], fmt)
            if dt.year > 101:
                return dt
        except Exception:
            pass

    m_year = re.search(r'\b(1[89]\d\d|20\d\d)\b', val)
    if m_year:
        try:
            return datetime(int(m_year.group(1)), 1, 1)
        except Exception:
            pass
    return None


# Calibre metadata.opf Parser
def parse_calibre_opf(opf_path: Path) -> dict:
    """Parse full metadata from a Calibre metadata.opf XML file."""
    meta = {
        "title": None,
        "author": None,
        "author_sort": None,
        "authors": [],
        "publisher": None,
        "comments": None,
        "tags": [],
        "series": None,
        "series_index": 1.0,
        "isbn": None,
        "uuid": None,
        "calibre_id": None,
        "pubdate": None,
        "language": None,
    }

    try:
        raw = opf_path.read_bytes()
        encoding = "utf-8"
        enc_m = re.search(rb'<\?xml[^>]+encoding=[\'"]([^\'"]+)[\'"]', raw[:200])
        if enc_m:
            try:
                encoding = enc_m.group(1).decode("ascii", errors="ignore").strip()
            except Exception:
                pass

        text = raw.decode(encoding, errors="replace")
        # Strip XML namespaces for reliable element parsing
        clean_text = re.sub(r'(</?)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)', r'\1\2', text)
        clean_text = re.sub(r'(\s)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)=', r'\1\2=', clean_text)
        clean_text = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', clean_text)

        root = ET.fromstring(clean_text)

        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()

            if tag == "title" and elem.text and not meta["title"]:
                meta["title"] = elem.text.strip()

            elif tag == "creator":
                name = elem.text.strip() if elem.text else ""
                file_as = elem.attrib.get("file-as")
                if file_as and not meta["author_sort"]:
                    meta["author_sort"] = file_as.strip()
                if name and name not in meta["authors"]:
                    meta["authors"].append(name)

            elif tag == "publisher" and elem.text and not meta["publisher"]:
                meta["publisher"] = elem.text.strip()

            elif tag == "language" and elem.text and not meta["language"]:
                meta["language"] = elem.text.strip().lower()[:10]

            elif tag == "description" and elem.text and not meta["comments"]:
                meta["comments"] = elem.text.strip()

            elif tag == "subject" and elem.text:
                s = elem.text.strip()
                if s and s not in meta["tags"]:
                    meta["tags"].append(s)

            elif tag == "date" and elem.text and not meta["pubdate"]:
                d_str = elem.text.strip()
                if not d_str.startswith("0101"):
                    meta["pubdate"] = d_str

            elif tag == "identifier":
                scheme = elem.attrib.get("scheme", "").lower()
                val = elem.text.strip() if elem.text else ""
                if scheme == "isbn" or re.match(r'^[0-9Xx-]{10,17}$', val):
                    meta["isbn"] = val.replace("-", "")
                elif scheme == "uuid":
                    meta["uuid"] = val
                elif scheme == "calibre":
                    try:
                        meta["calibre_id"] = int(val)
                    except ValueError:
                        pass

            elif tag == "meta":
                name = elem.attrib.get("name", "").lower()
                content = elem.attrib.get("content", "")
                if name.endswith("series") and content:
                    meta["series"] = content.strip()
                elif name.endswith("series_index") and content:
                    try:
                        meta["series_index"] = float(content.strip())
                    except ValueError:
                        pass
                elif name.endswith("title_sort") and content:
                    meta["title_sort"] = content.strip()

        if meta["authors"]:
            meta["author"] = meta["authors"][0]
            if not meta["author_sort"]:
                meta["author_sort"] = meta["author"]
        else:
            meta["author"] = "Unknown Author"
            meta["author_sort"] = "Unknown Author"
            meta["authors"] = [meta["author"]]

    except Exception:
        pass

    return meta


def extract_combined_dir_metadata(book_dir: Path) -> dict:
    """
    Extract metadata from metadata.opf and enrich any missing fields (pubdate, publisher,
    comments, series, tags, cover_bytes) from .fb2 or .epub files in the directory.
    """
    opf_file = book_dir / "metadata.opf"
    meta = parse_calibre_opf(opf_file) if opf_file.is_file() else {}

    need_enrichment = (
        not meta.get("title")
        or not meta.get("pubdate")
        or not meta.get("publisher")
        or not meta.get("comments")
        or not (book_dir / "cover.jpg").is_file()
    )

    if need_enrichment:
        fb2_file = None
        epub_file = None
        cbz_file = None
        other_file = None
        for item in book_dir.iterdir():
            if item.is_file():
                ext = item.suffix.lower()
                if ext in (".fb2", ".fbz") and not fb2_file:
                    fb2_file = item
                elif ext == ".epub" and not epub_file:
                    epub_file = item
                elif ext in (".cbz", ".cbr", ".cb7", ".cbt") and not cbz_file:
                    cbz_file = item
                elif ext in (".djvu", ".djv", ".docx", ".odt") or ext in importer_service.AUDIO_EXTS:
                    if not other_file:
                        other_file = item

        embedded_meta = {}
        if fb2_file:
            try:
                embedded_meta = parse_fb2_fast(fb2_file)
            except Exception:
                pass
        if epub_file and (not embedded_meta.get("pubdate") or not embedded_meta.get("publisher") or not embedded_meta.get("cover_bytes")):
            try:
                ep_meta = importer_service.parse_epub_metadata(epub_file)
                for k, v in ep_meta.items():
                    if v and not embedded_meta.get(k):
                        embedded_meta[k] = v
            except Exception:
                pass
        if cbz_file and (not embedded_meta.get("title") or not embedded_meta.get("cover_bytes")):
            try:
                cbz_meta = importer_service.parse_cbz_metadata(cbz_file)
                for k, v in cbz_meta.items():
                    if v and not embedded_meta.get(k):
                        embedded_meta[k] = v
            except Exception:
                pass
        if other_file and (not embedded_meta.get("title") or not embedded_meta.get("cover_bytes")):
            try:
                o_meta = importer_service.get_book_metadata(other_file, other_file.suffix.lower().lstrip("."))
                for k, v in o_meta.items():
                    if v and not embedded_meta.get(k):
                        embedded_meta[k] = v
            except Exception:
                pass

        for k in ("title", "author", "publisher", "comments", "series", "isbn", "language", "pubdate", "cover_bytes"):
            if embedded_meta.get(k) and not meta.get(k):
                meta[k] = embedded_meta[k]
        if embedded_meta.get("authors") and (not meta.get("authors") or meta.get("authors") == ["Unknown Author"]):
            meta["authors"] = embedded_meta["authors"]
            meta["author"] = embedded_meta["authors"][0]
            meta["author_sort"] = meta.get("author_sort") or meta["author"]
        if embedded_meta.get("tags") and not meta.get("tags"):
            meta["tags"] = embedded_meta["tags"]
        if embedded_meta.get("series_index") and meta.get("series_index") in (None, 1.0):
            meta["series_index"] = embedded_meta["series_index"]

    return meta


# In-Memory Cache for Foreign Keys
class FKCache:
    """Pre-caches authors, series, tags, publishers, and languages in memory."""
    def __init__(self, conn):
        self.authors: Dict[str, int] = {}
        self.series: Dict[str, int] = {}
        self.tags: Dict[str, int] = {}
        self.publishers: Dict[str, int] = {}
        self.languages: Dict[str, int] = {}
        self._load(conn)

    def _load(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT id, LOWER(name) FROM authors")
            for row in cur.fetchall():
                if row[1]:
                    self.authors[row[1]] = row[0]

            cur.execute("SELECT id, LOWER(name) FROM series")
            for row in cur.fetchall():
                if row[1]:
                    self.series[row[1]] = row[0]

            cur.execute("SELECT id, LOWER(name) FROM tags")
            for row in cur.fetchall():
                if row[1]:
                    self.tags[row[1]] = row[0]

            cur.execute("SELECT id, LOWER(name) FROM publishers")
            for row in cur.fetchall():
                if row[1]:
                    self.publishers[row[1]] = row[0]

            cur.execute("SELECT id, LOWER(lang_code) FROM languages")
            for row in cur.fetchall():
                if row[1]:
                    self.languages[row[1]] = row[0]

    def get_or_create_author(self, cur, name: str) -> int:
        clean = name.strip()
        lower = clean.lower()
        if lower in self.authors:
            return self.authors[lower]
        cur.execute("SELECT id FROM authors WHERE LOWER(name) = LOWER(%s) LIMIT 1", (clean,))
        row = cur.fetchone()
        if row:
            aid = row[0]
        else:
            cur.execute("INSERT INTO authors (name) VALUES (%s) RETURNING id", (clean,))
            aid = cur.fetchone()[0]
        self.authors[lower] = aid
        return aid

    def get_or_create_series(self, cur, name: str) -> int:
        clean = name.strip()
        lower = clean.lower()
        if lower in self.series:
            return self.series[lower]
        cur.execute("SELECT id FROM series WHERE LOWER(name) = LOWER(%s) LIMIT 1", (clean,))
        row = cur.fetchone()
        if row:
            sid = row[0]
        else:
            cur.execute("INSERT INTO series (name) VALUES (%s) RETURNING id", (clean,))
            sid = cur.fetchone()[0]
        self.series[lower] = sid
        return sid

    def get_or_create_tag(self, cur, name: str) -> int:
        clean = name.strip()
        lower = clean.lower()
        if lower in self.tags:
            return self.tags[lower]
        cur.execute("SELECT id FROM tags WHERE LOWER(name) = LOWER(%s) LIMIT 1", (clean,))
        row = cur.fetchone()
        if row:
            tid = row[0]
        else:
            cur.execute("INSERT INTO tags (name) VALUES (%s) RETURNING id", (clean,))
            tid = cur.fetchone()[0]
        self.tags[lower] = tid
        return tid

    def get_or_create_publisher(self, cur, name: str) -> int:
        clean = name.strip()
        lower = clean.lower()
        if lower in self.publishers:
            return self.publishers[lower]
        cur.execute("SELECT id FROM publishers WHERE LOWER(name) = LOWER(%s) LIMIT 1", (clean,))
        row = cur.fetchone()
        if row:
            pid = row[0]
        else:
            cur.execute("INSERT INTO publishers (name) VALUES (%s) RETURNING id", (clean,))
            pid = cur.fetchone()[0]
        self.publishers[lower] = pid
        return pid

    def get_or_create_language(self, cur, code: str) -> int:
        clean = code.strip().lower()[:10]
        CODE_MAP = {"ru": "rus", "en": "eng", "uk": "ukr", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "bg": "bul", "pl": "pol", "be": "bel"}
        clean = CODE_MAP.get(clean, clean)
        if clean in self.languages:
            return self.languages[clean]
        cur.execute("SELECT id FROM languages WHERE lang_code = %s", (clean,))
        row = cur.fetchone()
        if row:
            lid = row[0]
        else:
            cur.execute("INSERT INTO languages (lang_code) VALUES (%s) ON CONFLICT (lang_code) DO NOTHING RETURNING id", (clean,))
            res = cur.fetchone()
            if res:
                lid = res[0]
            else:
                cur.execute("SELECT id FROM languages WHERE lang_code = %s", (clean,))
                lid = cur.fetchone()[0]
        self.languages[clean] = lid
        return lid


SUPPORTED_FORMAT_EXTS = {
    ".epub": "EPUB",
    ".fb2": "FB2",
    ".fbz": "FBZ",
    ".pdf": "PDF",
    ".mobi": "MOBI",
    ".azw3": "AZW3",
    ".azw": "AZW",
    ".prc": "PRC",
    ".docx": "DOCX",
    ".rtf": "RTF",
    ".odt": "ODT",
    ".djvu": "DJVU",
    ".djv": "DJVU",
    ".cbr": "CBR",
    ".cbz": "CBZ",
    ".cb7": "CB7",
    ".cbt": "CBT",
    ".txt": "TXT",
    ".zip": "ZIP",
    ".m4b": "M4B",
    ".mp3": "MP3",
    ".m4a": "M4A",
    ".ogg": "OGG",
    ".opus": "OPUS",
    ".flac": "FLAC",
    ".wav": "WAV",
    ".aac": "AAC",
}


# Ingest a Missing Book Folder into PostgreSQL
def import_missing_book_dir(
    conn,
    fk_cache: FKCache,
    book_dir: Path,
    rel_path: str,
    db_ids: Set[int],
    fb2c_path: Optional[str] = None,
    do_convert: bool = True,
    keep_all_languages: bool = True
) -> Dict[str, Any]:
    """
    Given a book directory on disk that is missing from PostgreSQL,
    extracts metadata, converts FB2 to EPUB if needed, and inserts all records into the database.
    """
    meta = extract_combined_dir_metadata(book_dir)

    # Derive title & author from directory structure if OPF/embedded was missing or incomplete
    folder_name = book_dir.name
    author_folder = book_dir.parent.name

    # Try extracting Calibre ID from parentheses e.g. "Book Title (12345)"
    m_id = re.search(r'\((\d+)\)$', folder_name)

    # Determine Title
    title = meta.get("title")
    if not title:
        if m_id:
            title = folder_name[:m_id.start()].strip(' ._')
        else:
            title = folder_name.strip(' ._')
    if not title:
        title = "Unknown Title"

    # Determine Author
    primary_author = meta.get("author")
    if not primary_author or primary_author == "Unknown Author":
        primary_author = author_folder.strip(' ._') or "Unknown Author"

    author_sort = meta.get("author_sort") or primary_author

    # Write cover.jpg if extracted from ebook and not yet on disk
    cover_file = book_dir / "cover.jpg"
    if not cover_file.is_file() and meta.get("cover_bytes") and len(meta["cover_bytes"]) > 100:
        try:
            cover_file.write_bytes(meta["cover_bytes"])
            fix_ownership(cover_file)
        except Exception:
            pass

    # Auto-convert FB2 to EPUB if no EPUB exists in the directory
    if do_convert and not any(f.suffix.lower() == ".epub" for f in book_dir.iterdir() if f.is_file()):
        for f in list(book_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".fb2", ".fbz"):
                epub_out = convert_fb2_to_epub_fast(
                    fb2c_path=fb2c_path,
                    fb2_file=f,
                    out_dir=book_dir,
                    expected_epub_name=f.stem,
                    title=title,
                    author=primary_author
                )
                if epub_out and epub_out.is_file():
                    fix_ownership(epub_out)
                break

    # Discover format files in book directory
    formats_found = []
    has_cover = 1 if cover_file.is_file() else 0

    for item in book_dir.iterdir():
        if item.is_file():
            lower = item.name.lower()
            if lower == "cover.jpg":
                has_cover = 1
                continue
            ext = item.suffix.lower()
            if ext in SUPPORTED_FORMAT_EXTS:
                if lower.endswith(".fb2.zip"):
                    continue
                fmt_name = SUPPORTED_FORMAT_EXTS[ext]
                formats_found.append({
                    "format": fmt_name,
                    "size": item.stat().st_size,
                    "name": item.stem
                })

    if not formats_found:
        return {"status": "skipped", "reason": "No readable ebook files in folder"}

    # Safe ID Assignment: Check path and title existence to avoid race conditions
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        # 1. Double check path doesn't already exist
        cur.execute("SELECT id FROM books WHERE path = %s LIMIT 1", (rel_path,))
        if cur.fetchone():
            return {"status": "skipped", "reason": "Already in database"}

        # Resolve language code
        raw_lang = meta.get("language")
        if not raw_lang and title and re.search(r'[а-яА-ЯёЁ]', title):
            raw_lang = "rus"
        lang_code = None
        if raw_lang:
            clean_l = raw_lang.strip().lower()[:10]
            CODE_MAP = {"ru": "rus", "en": "eng", "uk": "ukr", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "bg": "bul", "pl": "pol", "be": "bel"}
            lang_code = CODE_MAP.get(clean_l, clean_l)

        # 2. Check title & author hash duplicate (language-aware)
        if title and primary_author:
            cur.execute("""
                SELECT b.id, l.lang_code
                FROM books b
                LEFT JOIN book_languages bl ON bl.book_id = b.id
                LEFT JOIN languages l ON l.id = bl.language_id
                WHERE md5(LOWER(TRIM(b.title)) || '|||' || LOWER(TRIM(b.author_sort))) = md5(%s || '|||' || %s)
                LIMIT 10
            """, (title.strip().lower(), author_sort.strip().lower()))
            existing_matches = cur.fetchall()
            for ex_id, ex_lang in existing_matches:
                # If keep_all_languages is True and languages differ, allow keeping both editions!
                if keep_all_languages and lang_code and ex_lang and ex_lang != lang_code:
                    continue
                return {"status": "skipped", "reason": f"Book already in database under another path (ID #{ex_id})"}

        # 3. Always allocate fresh unique ID from sequence
        cur.execute("SELECT nextval('books_id_seq')")
        book_id = cur.fetchone()[0]

        clean_pubdate = parse_pubdate(meta.get("pubdate"))
        book_uuid = meta.get("uuid") or str(uuid.uuid4())

        # Insert into books table with atomic conflict avoidance
        cur.execute("""
            INSERT INTO books (
                id, title, sort, timestamp, pubdate, series_index,
                author_sort, isbn, path, flags, uuid, has_cover, last_modified
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, 1, %s, %s, %s
            )
            ON CONFLICT (path) DO NOTHING
            RETURNING id
        """, (
            book_id,
            title,
            title.lower(),
            now,
            clean_pubdate,
            meta.get("series_index") or 1.0,
            author_sort,
            meta.get("isbn"),
            rel_path,
            book_uuid,
            has_cover,
            now
        ))
        res_row = cur.fetchone()
        if not res_row:
            return {"status": "skipped", "reason": "Already in database (conflict avoided)"}

        db_ids.add(book_id)

        # UUID Identifier
        cur.execute("""
            INSERT INTO identifiers (book_id, type, val)
            VALUES (%s, 'uuid', %s)
            ON CONFLICT DO NOTHING
        """, (book_id, book_uuid))

        # ISBN Identifier
        if meta.get("isbn"):
            cur.execute("""
                INSERT INTO identifiers (book_id, type, val)
                VALUES (%s, 'isbn', %s)
                ON CONFLICT DO NOTHING
            """, (book_id, meta["isbn"]))

        # Authors
        authors_to_link = meta.get("authors") or [primary_author]
        for a_name in authors_to_link:
            if not a_name or not a_name.strip():
                continue
            aid = fk_cache.get_or_create_author(cur, a_name)
            cur.execute("""
                INSERT INTO book_authors (book_id, author_id)
                VALUES (%s, %s)
                ON CONFLICT DO NOTHING
            """, (book_id, aid))

        # Formats (data table)
        for fmt in formats_found:
            cur.execute("""
                INSERT INTO data (book_id, format, uncompressed_size, name)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (book_id, format) DO UPDATE
                SET uncompressed_size = EXCLUDED.uncompressed_size,
                    name = EXCLUDED.name
            """, (book_id, fmt["format"], fmt["size"], fmt["name"]))

        # Comments (description)
        if meta.get("comments") and meta["comments"].strip():
            cur.execute("""
                INSERT INTO comments (book_id, text)
                VALUES (%s, %s)
                ON CONFLICT (book_id) DO UPDATE SET text = EXCLUDED.text
            """, (book_id, meta["comments"].strip()))

        # Publisher
        if meta.get("publisher") and meta["publisher"].strip():
            pid = fk_cache.get_or_create_publisher(cur, meta["publisher"])
            cur.execute("""
                INSERT INTO book_publishers (book_id, publisher_id)
                VALUES (%s, %s)
                ON CONFLICT (book_id, publisher_id) DO NOTHING
            """, (book_id, pid))

        # Series
        if meta.get("series") and meta["series"].strip():
            sid = fk_cache.get_or_create_series(cur, meta["series"])
            cur.execute("""
                INSERT INTO book_series (book_id, series_id)
                VALUES (%s, %s)
                ON CONFLICT (book_id, series_id) DO NOTHING
            """, (book_id, sid))

        # Tags
        for tag_name in meta.get("tags", []):
            if not tag_name or not tag_name.strip():
                continue
            tid = fk_cache.get_or_create_tag(cur, tag_name)
            cur.execute("""
                INSERT INTO book_tags (book_id, tag_id)
                VALUES (%s, %s)
                ON CONFLICT (book_id, tag_id) DO NOTHING
            """, (book_id, tid))

        # Language
        if lang_code:
            lid = fk_cache.get_or_create_language(cur, lang_code)
            cur.execute("""
                INSERT INTO book_languages (book_id, language_id)
                VALUES (%s, %s)
                ON CONFLICT (book_id, language_id) DO NOTHING
            """, (book_id, lid))

    fix_ownership(book_dir)
    return {
        "status": "added",
        "book_id": book_id,
        "title": title,
        "author": primary_author,
        "formats": [f["format"] for f in formats_found],
        "cover": bool(has_cover),
        "publisher": meta.get("publisher"),
    }


def backfill_existing_book(
    conn,
    fk_cache: FKCache,
    book_id: int,
    book_dir: Path,
    current_pubdate: Optional[datetime],
    has_publisher: bool,
    has_cover: int,
    dry_run: bool = False
) -> bool:
    """Backfill missing pubdate, publisher, description, and cover.jpg for an existing DB book."""
    if not book_dir.is_dir():
        return False

    need_pubdate = current_pubdate is None or current_pubdate.year <= 101
    need_publisher = not has_publisher
    need_cover = not has_cover or not (book_dir / "cover.jpg").is_file()

    if not (need_pubdate or need_publisher or need_cover):
        return False

    meta = extract_combined_dir_metadata(book_dir)
    updated = False

    if dry_run:
        if (need_pubdate and parse_pubdate(meta.get("pubdate"))) or (need_publisher and meta.get("publisher")) or (need_cover and meta.get("cover_bytes")):
            return True
        return False

    with conn.cursor() as cur:
        if need_pubdate and meta.get("pubdate"):
            dt = parse_pubdate(meta["pubdate"])
            if dt:
                cur.execute("UPDATE books SET pubdate = %s WHERE id = %s", (dt, book_id))
                updated = True

        if need_publisher and meta.get("publisher") and meta["publisher"].strip():
            pid = fk_cache.get_or_create_publisher(cur, meta["publisher"])
            cur.execute("""
                INSERT INTO book_publishers (book_id, publisher_id)
                VALUES (%s, %s)
                ON CONFLICT (book_id, publisher_id) DO NOTHING
            """, (book_id, pid))
            updated = True

        if need_cover and meta.get("cover_bytes") and len(meta["cover_bytes"]) > 100:
            cover_path = book_dir / "cover.jpg"
            try:
                cover_path.write_bytes(meta["cover_bytes"])
                fix_ownership(cover_path)
                cur.execute("UPDATE books SET has_cover = 1 WHERE id = %s", (book_id,))
                updated = True
            except Exception:
                pass

        if meta.get("comments") and meta["comments"].strip():
            cur.execute("""
                INSERT INTO comments (book_id, text)
                VALUES (%s, %s)
                ON CONFLICT (book_id) DO NOTHING
            """, (book_id, meta["comments"].strip()))

    return updated


def format_seconds(seconds: float) -> str:
    if seconds < 0 or seconds > 360000:
        return "--:--:--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# Main Scanner Entrypoint
def main():
    parser = argparse.ArgumentParser(
        description="Scan Calibre storage directory, compare with PostgreSQL, and import missing books."
    )
    parser.add_argument("source", nargs="?", default=settings.LIBRARY_ROOT, help="Storage directory (default: settings.LIBRARY_ROOT)")
    parser.add_argument("--dry-run", action="store_true", help="Audit and list missing books without modifying the database")
    parser.add_argument("--backfill-metadata", action="store_true", help="Also backfill missing pubdate, publisher, and cover for existing DB books")
    parser.add_argument("--no-convert", action="store_true", help="Skip automatic FB2-to-EPUB conversion for missing books")
    parser.add_argument("--limit", type=int, default=None, help="Import at most N missing books and exit")
    parser.add_argument("--batch-size", "-b", type=int, default=50, help="DB commit batch size (default: 50)")
    parser.add_argument("--keep-all-languages", action="store_true", default=True, help="Keep different language editions of books with the same title & author (default: True)")
    parser.add_argument("--no-keep-all-languages", action="store_false", dest="keep_all_languages", help="Skip books with same title & author even if language differs")
    args = parser.parse_args()

    library_root = Path(args.source).resolve()
    if not library_root.is_dir():
        print(f"[!] Error: Library directory does not exist: {library_root}")
        sys.exit(1)

    # Ensure only ONE instance of sync-library runs at any time
    lock_fd = None
    try:
        import fcntl
        import errno
        lock_file = Path("/tmp/sync_library.lock")
        try:
            lock_fd = open(lock_file, "a+")
        except PermissionError:
            lock_file = Path(f"/tmp/sync_library_{os.getuid()}.lock")
            lock_fd = open(lock_file, "a+")

        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as e:
        if isinstance(e, BlockingIOError) or getattr(e, 'errno', None) in (errno.EAGAIN, errno.EWOULDBLOCK):
            print("[!] Error: Another instance of sync-library is currently running.")
            print("    Only one library synchronizer process can run at a time to prevent duplicates.")
            sys.exit(1)
        else:
            print(f"[!] Warning: Could not acquire exclusive lock: {e}. Proceeding...")
    except ImportError:
        pass

    print("=" * 76)
    print(" 🔍 EBOOK LIBRARY STORAGE & DATABASE SYNCHRONIZER")
    print(f" Storage Path:  {library_root}")
    print(f" Dry Run Mode:  {'Yes (No DB modifications)' if args.dry_run else 'No (Missing books will be added)'}")
    print(f" Backfill Meta: {'Yes (Enrich existing books missing pubdate/publisher/cover)' if args.backfill_metadata else 'No (Use --backfill-metadata to enable)'}")
    print(f" Keep Languages:{'Yes (preserve all language editions)' if args.keep_all_languages else 'No (skip duplicates regardless of language)'}")
    print(f" Commit Batch:  {args.batch_size}")
    if args.limit:
        print(f" Import Limit:  {args.limit} books")
    print("=" * 76)

    fb2c_path = find_fb2c_binary()

    # 1. Connect and pre-load all paths and IDs from PostgreSQL
    print("[*] Connecting to PostgreSQL database...")
    conn = get_db_connection()
    if not args.dry_run:
        ensure_indexes_and_sequences(conn)

    print("[*] Pre-loading all existing book paths & IDs from database into memory...")
    t0 = time.time()
    db_paths: Set[str] = set()
    db_ids: Set[int] = set()
    existing_meta_map: Dict[str, Tuple[int, Optional[datetime], bool, int]] = {}

    with conn.cursor() as cur:
        if args.backfill_metadata:
            cur.execute("""
                SELECT b.id, b.path, b.pubdate,
                       EXISTS(SELECT 1 FROM book_publishers bp WHERE bp.book_id = b.id) AS has_pub,
                       b.has_cover
                FROM books b
            """)
            for row in cur.fetchall():
                db_ids.add(row[0])
                if row[1]:
                    norm_p = row[1].strip().replace('\\', '/')
                    db_paths.add(norm_p)
                    db_paths.add(norm_p.lower())
                    existing_meta_map[norm_p.lower()] = (row[0], row[2], bool(row[3]), row[4] or 0)
        else:
            cur.execute("SELECT id, path FROM books")
            for row in cur.fetchall():
                db_ids.add(row[0])
                if row[1]:
                    norm_p = row[1].strip().replace('\\', '/')
                    db_paths.add(norm_p)
                    db_paths.add(norm_p.lower())

    t1 = time.time()
    print(f"[✓] Loaded {len(db_paths)//2:,} book records in {t1 - t0:.2f}s (Instant O(1) matching ready)")

    fk_cache = FKCache(conn)

    # Error Log
    err_log_path = Path.cwd() / "sync_errors.log"
    try:
        err_file = open(err_log_path, "a", encoding="utf-8")
    except PermissionError:
        err_log_path = Path("/tmp/sync_errors.log")
        err_file = open(err_log_path, "a", encoding="utf-8")

    # Graceful shutdown
    shutdown_requested = False
    def sig_handler(sig, frame):
        nonlocal shutdown_requested
        print("\n\n[!] Interrupt received. Finishing current batch and saving...")
        shutdown_requested = True

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    # Statistics
    stats = {
        "author_dirs": 0,
        "book_dirs": 0,
        "already_in_db": 0,
        "backfilled": 0,
        "missing_found": 0,
        "added": 0,
        "skipped": 0,
        "errors": 0,
    }

    start_time = time.time()
    pending_commits = 0

    print("\n[*] Scanning storage directory tree for missing books...\n")

    try:
        with os.scandir(library_root) as author_it:
            for author_entry in author_it:
                if shutdown_requested:
                    break
                if not author_entry.is_dir(follow_symlinks=False):
                    continue

                author_name = author_entry.name
                stats["author_dirs"] += 1

                try:
                    with os.scandir(author_entry.path) as book_it:
                        for book_entry in book_it:
                            if shutdown_requested:
                                break
                            if not book_entry.is_dir(follow_symlinks=False):
                                continue

                            stats["book_dirs"] += 1
                            book_dir_name = book_entry.name
                            rel_path = f"{author_name}/{book_dir_name}".replace('\\', '/')

                            # O(1) Check if already in DB
                            if rel_path in db_paths or rel_path.lower() in db_paths:
                                stats["already_in_db"] += 1
                                if args.backfill_metadata and rel_path.lower() in existing_meta_map:
                                    b_id, b_pubdate, b_has_pub, b_has_cov = existing_meta_map[rel_path.lower()]
                                    try:
                                        if backfill_existing_book(
                                            conn, fk_cache, b_id, Path(book_entry.path),
                                            b_pubdate, b_has_pub, b_has_cov, dry_run=args.dry_run
                                        ):
                                            stats["backfilled"] += 1
                                            if not args.dry_run:
                                                pending_commits += 1
                                    except Exception as be:
                                        conn.rollback()
                                        err_file.write(f"[{datetime.now().isoformat()}] Backfill {rel_path}: {be}\n")
                                continue

                            # Also check folder ID in parentheses
                            m_id = re.search(r'\((\d+)\)$', book_dir_name)
                            if m_id and int(m_id.group(1)) in db_ids:
                                stats["already_in_db"] += 1
                                continue

                            # Missing Book Found!
                            stats["missing_found"] += 1
                            book_dir_path = Path(book_entry.path)

                            if args.dry_run:
                                stats["added"] += 1
                                sys.stdout.write(f"\r\033[K[DRY-RUN] Missing: {rel_path}\n")
                                sys.stdout.flush()
                            else:
                                try:
                                    res = import_missing_book_dir(
                                        conn=conn,
                                        fk_cache=fk_cache,
                                        book_dir=book_dir_path,
                                        rel_path=rel_path,
                                        db_ids=db_ids,
                                        fb2c_path=fb2c_path,
                                        do_convert=not args.no_convert,
                                        keep_all_languages=args.keep_all_languages
                                    )
                                    if res["status"] == "added":
                                        stats["added"] += 1
                                        db_paths.add(rel_path)
                                        db_paths.add(rel_path.lower())
                                        pending_commits += 1
                                    else:
                                        stats["skipped"] += 1
                                except Exception as e:
                                    stats["errors"] += 1
                                    conn.rollback()
                                    err_file.write(f"[{datetime.now().isoformat()}] {rel_path}: {e}\n")
                                    err_file.flush()

                            # Batch commit
                            if pending_commits >= args.batch_size:
                                conn.commit()
                                pending_commits = 0

                            # Update terminal status
                            now = time.time()
                            elapsed = now - start_time
                            rate = stats["book_dirs"] / elapsed if elapsed > 0 else 0.0

                            sys.stdout.write(
                                f"\r\033[K[Scanned: {stats['book_dirs']:,}] "
                                f"| In DB: {stats['already_in_db']:,} "
                                f"| 🚨 Missing: {stats['missing_found']:,} "
                                f"| 📚 Added: {stats['added']:,} "
                                f"| ✨ Backfilled: {stats['backfilled']:,} "
                                f"| {rate:.0f} dirs/s"
                            )
                            sys.stdout.flush()

                            if args.limit and stats["added"] >= args.limit:
                                print(f"\n[*] Reached limit of {args.limit} missing books.")
                                shutdown_requested = True
                                break

                except OSError:
                    continue

                if stats["author_dirs"] % 2500 == 0:
                    now = time.time()
                    elapsed = now - start_time
                    rate = stats["book_dirs"] / elapsed if elapsed > 0 else 0.0
                    sys.stdout.write(
                        f"\r\033[K[Authors: {stats['author_dirs']:,} | Books: {stats['book_dirs']:,}] "
                        f"| In DB: {stats['already_in_db']:,} "
                        f"| 🚨 Missing: {stats['missing_found']:,} "
                        f"| 📚 Added: {stats['added']:,} "
                        f"| ✨ Backfilled: {stats['backfilled']:,} "
                        f"| {rate:.0f} dirs/s"
                    )
                    sys.stdout.flush()

    finally:
        if pending_commits > 0:
            try:
                conn.commit()
            except Exception:
                pass
        conn.close()
        err_file.close()

    total_elapsed = time.time() - start_time

    print("\n\n" + "=" * 76)
    print(" 🏁 SCAN & SYNCHRONIZATION COMPLETE")
    print(f" Total Elapsed:       {format_seconds(total_elapsed)} ({total_elapsed:.1f}s)")
    print(f" Author Folders:      {stats['author_dirs']:,}")
    print(f" Total Book Folders:  {stats['book_dirs']:,}")
    print(f" Verified in DB:      {stats['already_in_db']:,}")
    print(f" Missing Discovered:  {stats['missing_found']:,}")
    print(f" Imported to DB:      {stats['added']:,}")
    if args.backfill_metadata:
        print(f" Metadata Backfilled: {stats['backfilled']:,}")
    print(f" Skipped (empty):     {stats['skipped']:,}")
    print(f" Errors:              {stats['errors']:,} (logged to {err_log_path.name})")
    print("=" * 76)


if __name__ == "__main__":
    main()
