import os
import re
import shutil
import tempfile
import zipfile
import uuid
import base64
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from ..settings import settings
except ImportError:
    from settings import settings

CYRILLIC_TO_LATIN = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'iu', 'я': 'ia',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
    'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Iu', 'Я': 'Ia'
}


def transliterate(text_str: str) -> str:
    """Transliterate Cyrillic characters to Latin for Calibre-compatible paths."""
    res = []
    for ch in text_str:
        res.append(CYRILLIC_TO_LATIN.get(ch, ch))
    return "".join(res)


def sanitize_filename(name: str, max_len: int = 96) -> str:
    """Sanitize string for safe filesystem usage."""
    if not name:
        return "Unknown"
    clean = transliterate(name)
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', clean)
    clean = re.sub(r'\s+', ' ', clean).strip(' ._')
    if not clean:
        clean = "Unknown"
    return clean[:max_len].strip()


def parse_pubdate(val: str | None) -> datetime | None:
    """Parse various date strings into a valid datetime."""
    if not val:
        return None
    val = str(val).strip()
    m_year = re.search(r'\b(1[89]\d\d|20\d\d)\b', val)
    if m_year:
        try:
            return datetime(int(m_year.group(1)), 1, 1)
        except Exception:
            pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(val[:19], fmt)
        except Exception:
            pass
    return None


def ensure_sequences(db: Session):
    """Ensure all required PostgreSQL sequences exist and are attached to table columns."""
    tables_cols = [
        ("books", "books_id_seq"),
        ("authors", "authors_id_seq"),
        ("data", "data_id_seq"),
        ("series", "series_id_seq"),
        ("tags", "tags_id_seq"),
        ("publishers", "publishers_id_seq"),
        ("identifiers", "identifiers_id_seq"),
    ]
    for tbl, seq in tables_cols:
        exists = db.execute(
            text("SELECT 1 FROM pg_sequences WHERE sequencename = :seq"),
            {"seq": seq}
        ).scalar()
        if not exists:
            max_val = db.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {tbl}")).scalar() or 0
            db.execute(text(f"CREATE SEQUENCE {seq} OWNED BY {tbl}.id"))
            db.execute(text(f"SELECT setval('{seq}', :m + 1, false)"), {"m": max_val})
            db.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN id SET DEFAULT nextval('{seq}')"))
            db.commit()


def parse_fb2_metadata(fb2_path: Path) -> dict:
    """Parse title, author, series, genre, and embedded cover directly from FB2 XML."""
    meta = {
        "title": None,
        "author": None,
        "authors": [],
        "tags": [],
        "series": None,
        "series_index": None,
        "comments": None,
        "publisher": None,
        "pubdate": None,
        "isbn": None,
        "language": None,
        "cover_bytes": None,
    }

    try:
        raw_bytes = fb2_path.read_bytes()
        encoding = "utf-8"
        enc_match = re.search(rb'<\?xml[^>]+encoding=[\'"]([^\'"]+)[\'"]', raw_bytes[:200])
        if enc_match:
            try:
                encoding = enc_match.group(1).decode("ascii").strip()
            except Exception:
                pass

        try:
            xml_text = raw_bytes.decode(encoding, errors="replace")
        except Exception:
            xml_text = raw_bytes.decode("utf-8", errors="replace")

        root = ET.fromstring(xml_text)

        cover_href = None
        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()

            if tag == "book-title" and elem.text and not meta["title"]:
                meta["title"] = elem.text.strip()

            elif tag == "author":
                first = ""
                middle = ""
                last = ""
                nick = ""
                for child in elem:
                    ctag = child.tag.split("}")[-1].lower()
                    if ctag == "first-name" and child.text:
                        first = child.text.strip()
                    elif ctag == "middle-name" and child.text:
                        middle = child.text.strip()
                    elif ctag == "last-name" and child.text:
                        last = child.text.strip()
                    elif ctag == "nickname" and child.text:
                        nick = child.text.strip()

                full_parts = [p for p in (first, middle, last) if p]
                full_name = " ".join(full_parts) if full_parts else nick
                if full_name:
                    meta["authors"].append(full_name)

            elif tag == "genre" and elem.text:
                g = elem.text.strip()
                if g and g not in meta["tags"]:
                    meta["tags"].append(g)

            elif tag == "sequence":
                s_name = elem.attrib.get("name") or elem.attrib.get("title")
                s_num = elem.attrib.get("number")
                if s_name:
                    meta["series"] = s_name.strip()
                if s_num:
                    try:
                        meta["series_index"] = float(s_num)
                    except ValueError:
                        pass

            elif tag == "annotation" and not meta["comments"]:
                paragraphs = [p.text.strip() for p in elem.iter() if p.text and p.text.strip()]
                if paragraphs:
                    meta["comments"] = "\n\n".join(paragraphs)

            elif tag == "isbn" and elem.text:
                meta["isbn"] = elem.text.strip().replace("-", "")

            elif tag in ("lang", "language") and elem.text and not meta["language"]:
                meta["language"] = elem.text.strip().lower()[:10]

            elif tag == "coverpage":
                for img in elem.iter():
                    if img.tag.split("}")[-1].lower() == "image":
                        for k, v in img.attrib.items():
                            if k.endswith("href"):
                                cover_href = v.lstrip("#")

        for elem in root.iter():
            if elem.tag.split("}")[-1].lower() == "binary":
                b_id = elem.attrib.get("id", "").strip().lstrip("#")
                if (cover_href and b_id == cover_href) or ("cover" in b_id.lower()) or (not meta["cover_bytes"] and elem.text):
                    try:
                        meta["cover_bytes"] = base64.b64decode(elem.text.strip())
                        break
                    except Exception:
                        pass

        if meta["authors"]:
            meta["author"] = meta["authors"][0]

    except Exception as e:
        print(f"Direct FB2 XML parse exception for {fb2_path.name}: {e}")

    # Regex fallbacks for maximum resilience if XML parsing failed or missed fields
    try:
        raw_text = fb2_path.read_text(encoding="utf-8", errors="replace")
        if not meta["title"]:
            m_title = re.search(r'<book-title[^>]*>(.*?)</book-title>', raw_text, re.DOTALL | re.IGNORECASE)
            if m_title:
                meta["title"] = re.sub(r'<[^>]+>', '', m_title.group(1)).strip()

        if not meta["authors"]:
            m_author = re.search(r'<author[^>]*>(.*?)</author>', raw_text, re.DOTALL | re.IGNORECASE)
            if m_author:
                fn = re.search(r'<first-name[^>]*>(.*?)</first-name>', m_author.group(1), re.DOTALL | re.IGNORECASE)
                ln = re.search(r'<last-name[^>]*>(.*?)</last-name>', m_author.group(1), re.DOTALL | re.IGNORECASE)
                first = fn.group(1).strip() if fn else ""
                last = ln.group(1).strip() if ln else ""
                full = f"{first} {last}".strip()
                if full:
                    meta["authors"].append(full)
                    meta["author"] = full
    except Exception:
        pass

    return meta


def parse_epub_metadata(epub_path: Path) -> dict:
    """Parse metadata and cover directly from an EPUB container."""
    meta = {
        "title": None,
        "author": None,
        "authors": [],
        "tags": [],
        "series": None,
        "series_index": None,
        "comments": None,
        "publisher": None,
        "pubdate": None,
        "isbn": None,
        "language": None,
        "cover_bytes": None,
    }

    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            container_xml = zf.read("META-INF/container.xml")
            c_root = ET.fromstring(container_xml)
            opf_path = None
            for rootfile in c_root.iter():
                if rootfile.tag.endswith("rootfile"):
                    opf_path = rootfile.attrib.get("full-path")
                    if opf_path:
                        break

            if not opf_path or opf_path not in zf.namelist():
                return meta

            opf_dir = Path(opf_path).parent.as_posix()
            if opf_dir == ".":
                opf_dir = ""

            opf_data = zf.read(opf_path)
            opf_root = ET.fromstring(opf_data)

            cover_id = None
            cover_href = None

            for elem in opf_root.iter():
                tag = elem.tag.split("}")[-1].lower()

                if tag == "title" and elem.text and not meta["title"]:
                    meta["title"] = elem.text.strip()

                elif tag == "creator" and elem.text:
                    author_name = elem.text.strip()
                    if author_name and author_name not in meta["authors"]:
                        meta["authors"].append(author_name)

                elif tag == "subject" and elem.text:
                    sub = elem.text.strip()
                    if sub and sub not in meta["tags"]:
                        meta["tags"].append(sub)

                elif tag == "description" and elem.text and not meta["comments"]:
                    meta["comments"] = elem.text.strip()

                elif tag == "publisher" and elem.text:
                    meta["publisher"] = elem.text.strip()

                elif tag == "language" and elem.text and not meta["language"]:
                    meta["language"] = elem.text.strip().lower()[:10]

                elif tag == "identifier" and elem.text:
                    id_val = elem.text.strip()
                    if re.match(r'^[0-9Xx-]{10,17}$', id_val):
                        meta["isbn"] = id_val.replace("-", "")

                elif tag == "meta":
                    name_attr = elem.attrib.get("name", "").lower()
                    prop_attr = elem.attrib.get("property", "").lower()
                    if name_attr == "cover":
                        cover_id = elem.attrib.get("content")
                    elif prop_attr == "belongs-to-collection":
                        meta["series"] = elem.text.strip() if elem.text else None
                    elif prop_attr == "group-position":
                        try:
                            meta["series_index"] = float(elem.text.strip())
                        except Exception:
                            pass

                elif tag == "item":
                    i_id = elem.attrib.get("id")
                    i_href = elem.attrib.get("href")
                    props = elem.attrib.get("properties", "").lower()
                    media_type = elem.attrib.get("media-type", "").lower()

                    if "cover-image" in props or i_id == "cover" or i_id == "cover-image":
                        cover_href = i_href
                    elif cover_id and i_id == cover_id:
                        cover_href = i_href
                    elif not cover_href and media_type.startswith("image/") and "cover" in (i_href or "").lower():
                        cover_href = i_href

            if cover_href:
                full_cover_path = f"{opf_dir}/{cover_href}".lstrip("/") if opf_dir else cover_href
                for name in zf.namelist():
                    if name.lower() == full_cover_path.lower() or name.lower().endswith(cover_href.lower()):
                        meta["cover_bytes"] = zf.read(name)
                        break

            if meta["authors"]:
                meta["author"] = meta["authors"][0]

    except Exception as e:
        print(f"Direct EPUB parse exception for {epub_path.name}: {e}")

    return meta


def convert_fb2_to_epub(fb2_path: Path, output_epub_path: Path, title: str | None = None, author: str | None = None) -> bool:
    """Convert FB2 to standard EPUB using Calibre's ebook-convert."""
    try:
        cmd = ["ebook-convert", str(fb2_path), str(output_epub_path)]
        if title:
            cmd.extend(["--title", title])
        if author:
            cmd.extend(["--authors", author])

        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False)
        return output_epub_path.is_file() and output_epub_path.stat().st_size > 0
    except Exception as e:
        print(f"Error converting {fb2_path} to EPUB: {e}")
        return False


def get_book_metadata(file_path: Path, ext: str) -> dict:
    """Get metadata by combining direct XML parsing with ebook-meta fallback."""
    meta = {}
    if ext in ("fb2", "fbz"):
        meta = parse_fb2_metadata(file_path)
    elif ext == "epub":
        meta = parse_epub_metadata(file_path)

    if not meta.get("title") or not meta.get("author"):
        try:
            cmd = ["ebook-meta", str(file_path)]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=60, check=False)
            for line in proc.stdout.splitlines():
                if ":" not in line:
                    continue
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if k == "Title" and not meta.get("title") and v:
                    meta["title"] = v
                elif k.startswith("Author") and not meta.get("author") and v:
                    raw_authors = [a.strip() for a in re.split(r'[&,;]', v) if a.strip()]
                    if raw_authors:
                        meta["authors"] = raw_authors
                        meta["author"] = raw_authors[0]
                elif k == "Tags" and not meta.get("tags") and v:
                    meta["tags"] = [t.strip() for t in v.split(",") if t.strip()]
                elif k == "Series" and not meta.get("series") and v:
                    m = re.match(r'^(.*?)(?:\s+#([\d.]+))?$', v)
                    if m:
                        meta["series"] = m.group(1).strip()
                        if m.group(2):
                            try:
                                meta["series_index"] = float(m.group(2))
                            except ValueError:
                                pass
                elif k == "Comments" and not meta.get("comments") and v:
                    meta["comments"] = v
        except Exception:
            pass

    if not meta.get("cover_bytes"):
        temp_cover = Path(tempfile.gettempdir()) / f"cov_{uuid.uuid4().hex}.jpg"
        try:
            cmd = ["ebook-meta", str(file_path), f"--get-cover={temp_cover}"]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40, check=False)
            if temp_cover.is_file() and temp_cover.stat().st_size > 0:
                meta["cover_bytes"] = temp_cover.read_bytes()
        except Exception:
            pass
        finally:
            if temp_cover.is_file():
                try:
                    os.remove(temp_cover)
                except OSError:
                    pass

    stem = file_path.stem
    if not meta.get("title") or meta["title"].strip() == "" or meta["title"].lower() == "unknown":
        if " - " in stem:
            parts = stem.split(" - ", 1)
            meta["author"] = meta.get("author") or parts[0].strip()
            meta["title"] = parts[1].strip()
        else:
            meta["title"] = stem.strip()

    if not meta.get("author") or not meta.get("authors"):
        meta["author"] = meta.get("author") or "Unknown Author"
        meta["authors"] = [meta["author"]]

    return meta


def find_duplicate_book(db: Session, title: str, author: str, isbn: str | None = None, lang_code: str | None = None) -> dict | None:
    """
    Search for an existing duplicate book by ISBN or Title + Author.
    Handles exact matches, transposed names (Last, First vs First Last), and title hashes.
    Respects language: books with different known languages are treated as separate editions.
    """
    # 1. Match by ISBN if present
    if isbn and len(isbn) >= 9:
        row = db.execute(text("""
            SELECT b.id, b.title, b.author_sort, b.path, b.has_cover
            FROM books b
            JOIN identifiers i ON b.id = i.book_id
            WHERE i.type = 'isbn' AND LOWER(i.val) = LOWER(:isbn)
            LIMIT 1
        """), {"isbn": isbn.replace("-", "").strip()}).fetchone()
        if row:
            return {
                "id": row.id,
                "title": row.title,
                "author": row.author_sort,
                "path": row.path,
                "has_cover": row.has_cover
            }

    clean_t = (title or "").strip().lower()
    clean_a = (author or "").strip().lower()

    if clean_t and clean_a and clean_a != "unknown author":
        # 2. Match by Title Hash and Author token similarity
        author_words = {w for w in re.split(r'[\s,._-]+', clean_a) if len(w) >= 3}
        candidates = db.execute(text("""
            SELECT b.id, b.title, b.author_sort, b.path, b.has_cover, l.lang_code
            FROM books b
            LEFT JOIN book_languages bl ON bl.book_id = b.id
            LEFT JOIN languages l ON l.id = bl.language_id
            WHERE md5(LOWER(TRIM(b.title))) = md5(:t)
            LIMIT 25
        """), {"t": clean_t}).fetchall()

        for c in candidates:
            # If both have languages recorded and they differ, they are separate language editions
            if lang_code and c.lang_code and c.lang_code != lang_code:
                continue

            c_clean = (c.author_sort or "").strip().lower()
            if c_clean == clean_a:
                return {"id": c.id, "title": c.title, "author": c.author_sort, "path": c.path, "has_cover": c.has_cover}
            c_words = {w for w in re.split(r'[\s,._-]+', c_clean) if len(w) >= 3}
            if author_words and c_words and (author_words & c_words):
                return {"id": c.id, "title": c.title, "author": c.author_sort, "path": c.path, "has_cover": c.has_cover}

        # 3. Fallback: match by combined title + author hash
        row = db.execute(text("""
            SELECT b.id, b.title, b.author_sort, b.path, b.has_cover, l.lang_code
            FROM books b
            LEFT JOIN book_languages bl ON bl.book_id = b.id
            LEFT JOIN languages l ON l.id = bl.language_id
            WHERE md5(LOWER(TRIM(b.title)) || '|||' || LOWER(TRIM(b.author_sort))) = md5(:t || '|||' || :a)
            LIMIT 1
        """), {"t": clean_t, "a": clean_a}).fetchone()
        if row:
            if not (lang_code and row.lang_code and row.lang_code != lang_code):
                return {
                    "id": row.id,
                    "title": row.title,
                    "author": row.author_sort,
                    "path": row.path,
                    "has_cover": row.has_cover
                }

    return None


def import_book_file(db: Session, file_path: Path, original_filename: str | None = None) -> dict:
    """
    Complete ingestion workflow for an individual book file:
    1. Parse metadata (FB2, EPUB, PDF, MOBI, etc.)
    2. Convert FB2 / FB2.ZIP to EPUB
    3. Copy book & cover to standard Calibre directory layout
    4. Insert and link all database records
    """
    ensure_sequences(db)
    orig_name = original_filename or file_path.name
    ext = file_path.suffix.lower().lstrip(".")

    work_dir = Path(tempfile.mkdtemp(prefix="book_import_"))
    try:
        epub_converted_path = None
        converted_from_fb2 = False

        actual_file_to_process = file_path
        effective_ext = ext

        if ext in ("zip", "fbz"):
            try:
                with zipfile.ZipFile(file_path, "r") as zf:
                    namelist = zf.namelist()
                    valid_names = [n for n in namelist if not n.startswith("__MACOSX") and not Path(n).name.startswith(".")]
                    epub_names = [n for n in valid_names if n.lower().endswith(".epub")]
                    fb2_names = [n for n in valid_names if n.lower().endswith(".fb2")]
                    pdf_names = [n for n in valid_names if n.lower().endswith(".pdf")]
                    mobi_names = [n for n in valid_names if n.lower().endswith((".mobi", ".azw3"))]

                    if epub_names:
                        extracted = zf.extract(epub_names[0], path=work_dir)
                        actual_file_to_process = Path(extracted)
                        effective_ext = "epub"
                    elif fb2_names:
                        extracted = zf.extract(fb2_names[0], path=work_dir)
                        actual_file_to_process = Path(extracted)
                        effective_ext = "fb2"
                    elif pdf_names:
                        extracted = zf.extract(pdf_names[0], path=work_dir)
                        actual_file_to_process = Path(extracted)
                        effective_ext = "pdf"
                    elif mobi_names:
                        extracted = zf.extract(mobi_names[0], path=work_dir)
                        actual_file_to_process = Path(extracted)
                        effective_ext = Path(mobi_names[0]).suffix.lower().lstrip(".")
                    elif "mimetype" in namelist or any(n.endswith(".opf") for n in namelist):
                        # The zip file itself is an EPUB archive
                        effective_ext = "epub"
                        actual_file_to_process = file_path
            except Exception:
                pass

        if effective_ext in ("fb2", "fbz"):
            actual_fb2 = actual_file_to_process
            meta = parse_fb2_metadata(actual_fb2)

            title_hint = meta.get("title") or Path(orig_name).stem
            author_hint = meta.get("author") or "Unknown Author"

            target_epub = work_dir / f"{actual_fb2.stem}.epub"
            if convert_fb2_to_epub(actual_fb2, target_epub, title=title_hint, author=author_hint):
                epub_converted_path = target_epub
                converted_from_fb2 = True
                epub_meta = parse_epub_metadata(target_epub)
                if epub_meta.get("cover_bytes") and not meta.get("cover_bytes"):
                    meta["cover_bytes"] = epub_meta["cover_bytes"]
        else:
            meta = get_book_metadata(actual_file_to_process, effective_ext)

        title = (meta.get("title") or Path(orig_name).stem).strip()
        primary_author = (meta.get("author") or "Unknown Author").strip()

        # Language resolution
        lang_code = meta.get("language")
        if not lang_code and title and re.search(r'[а-яА-ЯёЁ]', title):
            lang_code = "rus"
        if lang_code:
            lang_code = lang_code.strip().lower()[:10]
            CODE_MAP = {"ru": "rus", "en": "eng", "uk": "ukr", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "bg": "bul", "pl": "pol", "be": "bel"}
            lang_code = CODE_MAP.get(lang_code, lang_code)

        # Check for existing duplicate book (respecting language)
        existing_book = find_duplicate_book(db, title, primary_author, meta.get("isbn"), lang_code=lang_code)
        is_duplicate = existing_book is not None

        if is_duplicate:
            book_id = existing_book["id"]
            rel_path = existing_book["path"]
            dest_dir = Path(settings.LIBRARY_ROOT) / rel_path
            dest_dir.mkdir(parents=True, exist_ok=True)
            # Clean older ebook files with outdated names in this directory
            for old_file in dest_dir.iterdir():
                if old_file.is_file() and old_file.suffix.lower() in ('.epub', '.fb2', '.fbz', '.zip', '.mobi', '.pdf'):
                    try:
                        old_file.unlink()
                    except OSError:
                        pass
        else:
            book_id = db.execute(text("SELECT nextval('books_id_seq')")).scalar()
            author_folder = sanitize_filename(primary_author, max_len=64)
            title_folder = f"{sanitize_filename(title, max_len=64)} ({book_id})"
            rel_path = f"{author_folder}/{title_folder}"
            dest_dir = Path(settings.LIBRARY_ROOT) / author_folder / title_folder
            dest_dir.mkdir(parents=True, exist_ok=True)

        base_file_name = sanitize_filename(f"{title} - {primary_author}", max_len=96)
        formats_imported = []

        # Copy converted or extracted ebook format files into book directory
        if epub_converted_path and epub_converted_path.is_file():
            epub_dest = dest_dir / f"{base_file_name}.epub"
            shutil.copy2(epub_converted_path, epub_dest)
            formats_imported.append({
                "format": "EPUB",
                "size": epub_dest.stat().st_size,
                "name": base_file_name
            })
        elif effective_ext in ("epub", "pdf", "mobi", "azw3"):
            dest_file = dest_dir / f"{base_file_name}.{effective_ext}"
            if actual_file_to_process.resolve() != dest_file.resolve():
                shutil.copy2(actual_file_to_process, dest_file)
            formats_imported.append({
                "format": effective_ext.upper(),
                "size": dest_file.stat().st_size,
                "name": base_file_name
            })
        elif ext not in ("fb2", "fbz", "fb2.zip", "zip"):
            orig_dest_file = dest_dir / f"{base_file_name}.{ext}"
            if file_path.resolve() != orig_dest_file.resolve():
                shutil.copy2(file_path, orig_dest_file)
            formats_imported.append({
                "format": ext.upper(),
                "size": orig_dest_file.stat().st_size,
                "name": base_file_name
            })

        # Handle Cover (overwrite if new cover present, or preserve existing)
        has_cover = existing_book["has_cover"] if is_duplicate else 0
        if meta.get("cover_bytes") and len(meta["cover_bytes"]) > 100:
            dest_cover = dest_dir / "cover.jpg"
            dest_cover.write_bytes(meta["cover_bytes"])
            has_cover = 1

        now = datetime.now(timezone.utc)
        clean_pub = parse_pubdate(meta.get("pubdate"))
        if is_duplicate:
            # Overwrite & update existing book record
            db.execute(text("""
                UPDATE books SET
                    title = :title,
                    sort = :sort,
                    author_sort = :author_sort,
                    pubdate = COALESCE(:pubdate, pubdate),
                    series_index = COALESCE(:s_idx, series_index),
                    isbn = COALESCE(:isbn, isbn),
                    has_cover = :has_cover,
                    last_modified = :last_mod
                WHERE id = :id
            """), {
                "id": book_id,
                "title": title,
                "sort": title.lower(),
                "author_sort": primary_author,
                "pubdate": clean_pub,
                "s_idx": meta.get("series_index"),
                "isbn": meta.get("isbn"),
                "has_cover": has_cover,
                "last_mod": now,
            })
        else:
            book_uuid = str(uuid.uuid4())
            db.execute(text("""
                INSERT INTO books (
                    id, title, sort, timestamp, pubdate, series_index,
                    author_sort, isbn, path, flags, uuid, has_cover, last_modified
                ) VALUES (
                    :id, :title, :sort, :ts, :pubdate, :s_idx,
                    :author_sort, :isbn, :path, 1, :uuid, :has_cover, :last_mod
                )
            """), {
                "id": book_id,
                "title": title,
                "sort": title.lower(),
                "ts": now,
                "pubdate": clean_pub,
                "s_idx": meta.get("series_index") or 1.0,
                "author_sort": primary_author,
                "isbn": meta.get("isbn"),
                "path": rel_path,
                "uuid": book_uuid,
                "has_cover": has_cover,
                "last_mod": now,
            })
            db.execute(text("""
                INSERT INTO identifiers (book_id, type, val)
                VALUES (:bid, 'uuid', :u)
            """), {"bid": book_id, "u": book_uuid})

        # Authors & book_authors (clean old links on duplicate overwrite)
        if is_duplicate:
            db.execute(text("DELETE FROM book_authors WHERE book_id = :bid"), {"bid": book_id})
        authors_to_link = meta.get("authors") or [primary_author]
        for a_name_raw in authors_to_link:
            a_name = a_name_raw.strip()
            if not a_name:
                continue
            author_row = db.execute(
                text("SELECT id FROM authors WHERE LOWER(name) = LOWER(:name) LIMIT 1"),
                {"name": a_name}
            ).fetchone()
            if author_row:
                aid = author_row.id
            else:
                aid = db.execute(text("""
                    INSERT INTO authors (name) VALUES (:name) RETURNING id
                """), {"name": a_name}).scalar()

            db.execute(text("""
                INSERT INTO book_authors (book_id, author_id)
                VALUES (:bid, :aid)
                ON CONFLICT (book_id, author_id) DO NOTHING
            """), {"bid": book_id, "aid": aid})

        # Data table: clean old format records on duplicate overwrite and insert fresh
        if is_duplicate:
            db.execute(text("DELETE FROM data WHERE book_id = :bid"), {"bid": book_id})
        for fmt_info in formats_imported:
            db.execute(text("""
                INSERT INTO data (book_id, format, uncompressed_size, name)
                VALUES (:bid, :fmt, :sz, :name)
            """), {
                "bid": book_id,
                "fmt": fmt_info["format"],
                "sz": fmt_info["size"],
                "name": fmt_info["name"]
            })

        # Comments (description): update or insert
        if meta.get("comments") and meta["comments"].strip():
            db.execute(text("""
                INSERT INTO comments (book_id, text)
                VALUES (:bid, :txt)
                ON CONFLICT (book_id) DO UPDATE SET text = EXCLUDED.text
            """), {"bid": book_id, "txt": meta["comments"].strip()})

        # Series & book_series (clean old link on duplicate overwrite)
        if meta.get("series") and meta["series"].strip():
            if is_duplicate:
                db.execute(text("DELETE FROM book_series WHERE book_id = :bid"), {"bid": book_id})
            s_name = meta["series"].strip()
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
            """), {"bid": book_id, "sid": sid})

        # Tags & book_tags (clean old links on duplicate overwrite)
        if meta.get("tags"):
            if is_duplicate:
                db.execute(text("DELETE FROM book_tags WHERE book_id = :bid"), {"bid": book_id})
            for tag_name in meta["tags"]:
                t_name = tag_name.strip()
                if not t_name:
                    continue
                t_row = db.execute(
                    text("SELECT id FROM tags WHERE LOWER(name) = LOWER(:t) LIMIT 1"),
                    {"t": t_name}
                ).fetchone()
                if t_row:
                    tid = t_row.id
                else:
                    tid = db.execute(text("""
                        INSERT INTO tags (name) VALUES (:t) RETURNING id
                    """), {"t": t_name}).scalar()

                db.execute(text("""
                    INSERT INTO book_tags (book_id, tag_id)
                    VALUES (:bid, :tid)
                    ON CONFLICT (book_id, tag_id) DO NOTHING
                """), {"bid": book_id, "tid": tid})

        # Publisher & book_publishers (clean old link on duplicate overwrite)
        if meta.get("publisher") and meta["publisher"].strip():
            if is_duplicate:
                db.execute(text("DELETE FROM book_publishers WHERE book_id = :bid"), {"bid": book_id})
            p_name = meta["publisher"].strip()
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
            """), {"bid": book_id, "pid": pid})

        # Identifiers: ISBN (clean old isbn on duplicate overwrite)
        if meta.get("isbn"):
            if is_duplicate:
                db.execute(text("DELETE FROM identifiers WHERE book_id = :bid AND type = 'isbn'"), {"bid": book_id})
            db.execute(text("""
                INSERT INTO identifiers (book_id, type, val)
                VALUES (:bid, 'isbn', :isbn)
                ON CONFLICT DO NOTHING
            """), {"bid": book_id, "isbn": meta["isbn"]})

        # Language handling
        lang_code = meta.get("language")
        if not lang_code and title and re.search(r'[а-яА-ЯёЁ]', title):
            lang_code = "rus"
        if lang_code:
            lang_code = lang_code.strip().lower()[:10]
            CODE_MAP = {"ru": "rus", "en": "eng", "uk": "ukr", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "bg": "bul", "pl": "pol", "be": "bel"}
            lang_code = CODE_MAP.get(lang_code, lang_code)
            l_row = db.execute(text("SELECT id FROM languages WHERE lang_code = :c LIMIT 1"), {"c": lang_code}).fetchone()
            if l_row:
                lid = l_row.id
            else:
                lid = db.execute(text("INSERT INTO languages (lang_code) VALUES (:c) ON CONFLICT (lang_code) DO NOTHING RETURNING id"), {"c": lang_code}).scalar()
                if not lid:
                    lid = db.execute(text("SELECT id FROM languages WHERE lang_code = :c"), {"c": lang_code}).scalar()
            if lid:
                db.execute(text("""
                    INSERT INTO book_languages (book_id, language_id)
                    VALUES (:bid, :lid)
                    ON CONFLICT (book_id, language_id) DO NOTHING
                """), {"bid": book_id, "lid": lid})

        db.commit()

        return {
            "status": "overwritten" if is_duplicate else "created",
            "is_duplicate": is_duplicate,
            "id": book_id,
            "title": title,
            "author": primary_author,
            "formats": [f["format"] for f in formats_imported],
            "converted_from_fb2": converted_from_fb2,
            "cover": bool(has_cover),
        }

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def scan_and_import_directory(db: Session, dir_path: Path) -> dict:
    """Recursively scan a directory on the server and import all discovered ebook files."""
    SUPPORTED_EXTS = {".epub", ".fb2", ".fbz", ".pdf", ".mobi", ".azw3", ".zip"}
    results = {"imported": [], "failed": []}
    if not dir_path.is_dir():
        return {"error": f"Directory not found: {dir_path}"}

    for file_path in dir_path.rglob("*"):
        if file_path.is_file():
            ext = file_path.suffix.lower()
            if ext in SUPPORTED_EXTS or file_path.name.lower().endswith(".fb2.zip"):
                try:
                    res = import_book_file(db, file_path, file_path.name)
                    results["imported"].append(res)
                except Exception as e:
                    results["failed"].append({"file": file_path.name, "error": str(e)})

    return results
