import os
import re
import html
import shutil
import tempfile
import zipfile
import uuid
import base64
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger("athenea.importer")

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
    'і': 'i', 'ї': 'yi', 'є': 'ye', 'ґ': 'g',
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
    'Ж': 'Zh', 'З': 'Z', 'И': 'I', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'Kh', 'Ц': 'Ts', 'Ч': 'Ch', 'Ш': 'Sh', 'Щ': 'Shch',
    'Ъ': '', 'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'Iu', 'Я': 'Ia',
    'І': 'I', 'Ї': 'Yi', 'Є': 'Ye', 'Ґ': 'G',
    '«': '', '»': '', '“': '', '”': '', '’': "'", '`': "'", '—': '-', '–': '-',
    '№': 'No'
}


def transliterate(text_str: str) -> str:
    """Transliterate Cyrillic, Ukrainian and accented characters to safe ASCII for Calibre filesystem paths."""
    res = []
    for ch in str(text_str or ""):
        res.append(CYRILLIC_TO_LATIN.get(ch, ch))
    text = "".join(res)
    # Decompose accents and special unicode marks (é -> e, ñ -> n)
    normalized = unicodedata.normalize('NFKD', text)
    # Guarantee 100% pure ASCII for filesystem safety across any OS locale
    safe_ascii = "".join(c for c in normalized if ord(c) < 128)
    return safe_ascii


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
        ("languages", "languages_id_seq"),
    ]
    for tbl, seq in tables_cols:
        try:
            exists = db.execute(
                text("SELECT 1 FROM pg_sequences WHERE sequencename = :seq"),
                {"seq": seq}
            ).scalar()
            if not exists:
                max_val = db.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {tbl}")).scalar() or 0
                db.execute(text(f"CREATE SEQUENCE IF NOT EXISTS {seq} OWNED BY {tbl}.id"))
                db.execute(text(f"SELECT setval('{seq}', :m + 1, false)"), {"m": max_val})
                db.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN id SET DEFAULT nextval('{seq}')"))
                db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"Failed to ensure sequence {seq} for {tbl}: {e}")

    try:
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_languages_lang_code ON languages (lang_code)"))
        # Deduplicate any duplicate format rows in data table and enforce unique index
        db.execute(text("""
            DELETE FROM data a
            USING data b
            WHERE a.id > b.id
              AND a.book_id = b.book_id
              AND UPPER(a.format) = UPPER(b.format)
        """))
        db.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_data_book_format ON data (book_id, format)"))
        db.commit()
    except Exception as e:
        db.rollback()


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


def native_fb2_to_epub(fb2_path: Path, output_epub_path: Path, title: str | None = None, author: str | None = None) -> bool:
    """
    Pure-Python converter from FB2 XML to standard EPUB 2/3.
    Extracts chapters, nested sections, inline styles, poems, epigraphs, and embedded base64 images.
    Used as high-performance zero-dependency fallback when Calibre is not installed.
    """
    try:
        raw_bytes = fb2_path.read_bytes()
        if not raw_bytes:
            return False

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

        # Sanitize unescaped ampersands that violate XML spec
        clean_xml = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', xml_text)

        try:
            root = ET.fromstring(clean_xml)
        except Exception:
            idx = clean_xml.find("<FictionBook")
            if idx > 0:
                clean_xml = clean_xml[idx:]
            root = ET.fromstring(clean_xml)

        def strip_ns(t: str) -> str:
            return t.split("}")[-1].lower() if "}" in t else t.lower()

        book_title = title
        book_author = author
        book_lang = "ru" if re.search(r'[а-яА-ЯёЁ]', clean_xml[:4000]) else "en"
        cover_image_id = None

        for desc in root:
            if strip_ns(desc.tag) == "description":
                for ti in desc:
                    if strip_ns(ti.tag) == "title-info":
                        for c in ti:
                            ctag = strip_ns(c.tag)
                            if ctag == "book-title" and not book_title and c.text:
                                book_title = c.text.strip()
                            elif ctag == "lang" and c.text:
                                book_lang = c.text.strip().lower()[:10]
                            elif ctag == "coverpage":
                                for img in c.iter():
                                    if strip_ns(img.tag) == "image":
                                        for k, v in img.attrib.items():
                                            if k.endswith("href"):
                                                cover_image_id = v.lstrip("#")
                            elif ctag == "author" and not book_author:
                                parts = [p.text.strip() for p in c if p.text and p.text.strip()]
                                if parts:
                                    book_author = " ".join(parts)

        book_title = book_title or fb2_path.stem
        book_author = book_author or "Unknown Author"

        # Extract binary attachments (embedded images)
        images = {}
        cover_filename = None
        for elem in root:
            if strip_ns(elem.tag) == "binary":
                b_id = elem.attrib.get("id", "").strip().lstrip("#")
                content_type = elem.attrib.get("content-type", "image/jpeg")
                if not b_id or not elem.text:
                    continue
                try:
                    img_data = base64.b64decode(elem.text.strip())
                    ext_img = "jpg"
                    if "png" in content_type:
                        ext_img = "png"
                    elif "gif" in content_type:
                        ext_img = "gif"
                    elif "webp" in content_type:
                        ext_img = "webp"

                    safe_name = f"img_{re.sub(r'[^a-zA-Z0-9_-]', '_', b_id)}.{ext_img}"
                    images[b_id] = {
                        "name": safe_name,
                        "data": img_data,
                        "mime": content_type
                    }
                    if (cover_image_id and b_id == cover_image_id) or (not cover_filename and "cover" in b_id.lower()):
                        cover_filename = safe_name
                except Exception:
                    pass

        def inner_html(node) -> str:
            out = html.escape(node.text or "")
            for child in node:
                ctag = strip_ns(child.tag)
                c_inner = inner_html(child)
                if ctag in ("emphasis", "i"):
                    out += f"<em>{c_inner}</em>"
                elif ctag in ("strong", "b"):
                    out += f"<strong>{c_inner}</strong>"
                elif ctag in ("strikethrough", "s"):
                    out += f"<s>{c_inner}</s>"
                elif ctag == "sub":
                    out += f"<sub>{c_inner}</sub>"
                elif ctag == "sup":
                    out += f"<sup>{c_inner}</sup>"
                elif ctag == "a":
                    hr = child.attrib.get("href", "#")
                    out += f'<a href="{hr}">{c_inner}</a>'
                elif ctag == "image":
                    hr = None
                    for k, v in child.attrib.items():
                        if k.endswith("href"):
                            hr = v.lstrip("#")
                    if hr and hr in images:
                        out += f'<img src="../images/{images[hr]["name"]}" alt="" />'
                else:
                    out += c_inner
                if child.tail:
                    out += html.escape(child.tail)
            return out

        def elem_to_html(element) -> str:
            res = []
            for node in element:
                ntag = strip_ns(node.tag)
                tail = (node.tail or "").strip()

                if ntag == "p":
                    inner = inner_html(node)
                    res.append(f"<p>{inner}</p>")
                elif ntag == "title":
                    inner = inner_html(node).strip()
                    if inner:
                        res.append(f"<h2>{inner}</h2>")
                elif ntag == "subtitle":
                    inner = inner_html(node).strip()
                    if inner:
                        res.append(f"<h3>{inner}</h3>")
                elif ntag == "empty-line":
                    res.append('<p class="empty-line">&nbsp;</p>')
                elif ntag in ("epigraph", "cite"):
                    inner = elem_to_html(node)
                    res.append(f"<blockquote>{inner}</blockquote>")
                elif ntag == "poem":
                    inner = elem_to_html(node)
                    res.append(f'<div class="poem">{inner}</div>')
                elif ntag == "stanza":
                    inner = elem_to_html(node)
                    res.append(f'<div class="stanza">{inner}</div>')
                elif ntag == "v":
                    inner = inner_html(node)
                    res.append(f'<p class="verse">{inner}</p>')
                elif ntag == "image":
                    href = None
                    for k, v in node.attrib.items():
                        if k.endswith("href"):
                            href = v.lstrip("#")
                    if href and href in images:
                        res.append(f'<div class="illustration"><img src="../images/{images[href]["name"]}" alt="" /></div>')
                elif ntag == "section":
                    res.append(elem_to_html(node))
                else:
                    inner = inner_html(node)
                    if inner:
                        res.append(f"<p>{inner}</p>")
                if tail:
                    res.append(f"<p>{html.escape(tail)}</p>")
            return "\n".join(res)

        chapters = []
        for body in root:
            if strip_ns(body.tag) != "body":
                continue
            b_name = body.attrib.get("name", "")
            is_notes = "notes" in b_name.lower() or "comments" in b_name.lower()

            sections = [s for s in body if strip_ns(s.tag) == "section"]
            if sections:
                for idx, sec in enumerate(sections, 1):
                    sec_title = f"Chapter {idx}" if not is_notes else f"Note {idx}"
                    t_elem = None
                    for c in sec:
                        if strip_ns(c.tag) == "title":
                            t_elem = c
                            break
                    if t_elem is not None:
                        t_parts = [inner_html(p) for p in t_elem if strip_ns(p.tag) == "p"]
                        if t_parts:
                            sec_title = re.sub(r'<[^>]+>', '', " - ".join(t_parts)).strip()
                        elif t_elem.text:
                            sec_title = t_elem.text.strip()
                    sec_html = elem_to_html(sec)
                    if sec_html.strip():
                        chapters.append((sec_title, sec_html))
            else:
                b_html = elem_to_html(body)
                if b_html.strip():
                    chapters.append(("Notes" if is_notes else book_title, b_html))

        if not chapters:
            return False

        # Generate XHTML pages
        chapter_files = []
        for i, (ch_title, ch_body) in enumerate(chapters, 1):
            fname = f"chapter_{i:04d}.xhtml"
            header_html = f"<h2>{html.escape(ch_title)}</h2>\n  " if not ch_body.lstrip().startswith("<h") else ""
            xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{book_lang}">
<head>
  <title>{html.escape(ch_title)}</title>
  <link rel="stylesheet" type="text/css" href="../styles/style.css" />
</head>
<body>
  {header_html}{ch_body}
</body>
</html>'''
            chapter_files.append({
                "filename": fname,
                "title": ch_title,
                "content": xhtml.encode("utf-8")
            })

        css_content = b'''body {
  font-family: serif;
  margin: 5%;
  line-height: 1.5;
  color: #1a1a1a;
  background-color: #fff;
}
h1, h2, h3, h4 {
  text-align: center;
  font-weight: normal;
  margin: 1.5em 0 1em 0;
}
p {
  text-indent: 1.5em;
  margin: 0 0 0.5em 0;
  text-align: justify;
}
p.empty-line {
  margin: 0.8em 0;
  text-indent: 0;
}
blockquote {
  margin: 1em 2em;
  font-style: italic;
  color: #555;
}
.poem {
  margin: 1em 2em;
  font-style: italic;
}
.stanza {
  margin-bottom: 0.8em;
}
p.verse {
  text-indent: 0;
  margin: 0.2em 0;
}
.illustration {
  text-align: center;
  margin: 1.5em auto;
}
img {
  max-width: 100%;
  height: auto;
}
'''
        book_uuid = str(uuid.uuid4())
        container_xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''

        # Build content.opf
        manifest_items = [
            '<item id="style" href="styles/style.css" media-type="text/css"/>',
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        ]
        spine_items = []
        for i, ch in enumerate(chapter_files, 1):
            cid = f"ch_{i:04d}"
            manifest_items.append(f'<item id="{cid}" href="text/{ch["filename"]}" media-type="application/xhtml+xml"/>')
            spine_items.append(f'<itemref idref="{cid}"/>')

        for img_id, img_info in images.items():
            manifest_items.append(f'<item id="img_{re.sub(r"[^a-zA-Z0-9_-]", "_", img_id)}" href="images/{img_info["name"]}" media-type="{img_info["mime"]}"/>')

        manifest_str = "\n    ".join(manifest_items)
        spine_str = "\n    ".join(spine_items)

        cover_meta = f'<meta name="cover" content="img_{re.sub(r"[^a-zA-Z0-9_-]", "_", cover_image_id)}"/>' if cover_image_id and cover_image_id in images else ''

        opf_content = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookID">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{html.escape(book_title)}</dc:title>
    <dc:creator opf:role="aut">{html.escape(book_author)}</dc:creator>
    <dc:language>{book_lang}</dc:language>
    <dc:identifier id="BookID" opf:scheme="UUID">{book_uuid}</dc:identifier>
    {cover_meta}
  </metadata>
  <manifest>
    {manifest_str}
  </manifest>
  <spine toc="ncx">
    {spine_str}
  </spine>
</package>'''.encode("utf-8")

        # Build toc.ncx
        nav_points = []
        for i, ch in enumerate(chapter_files, 1):
            nav_points.append(f'''    <navPoint id="nav_{i}" playOrder="{i}">
      <navLabel><text>{html.escape(ch["title"])}</text></navLabel>
      <content src="text/{ch["filename"]}"/>
    </navPoint>''')
        nav_str = "\n".join(nav_points)

        ncx_content = f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE ncx PUBLIC "-//NISO//DTD ncx 2005-1//EN" "http://www.daisy.org/z3986/2005/ncx-2005-1.dtd">
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="{book_uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{html.escape(book_title)}</text></docTitle>
  <docAuthor><text>{html.escape(book_author)}</text></docAuthor>
  <navMap>
{nav_str}
  </navMap>
</ncx>'''.encode("utf-8")

        output_epub_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_epub_path, "w") as zf:
            # 1. mimetype MUST be uncompressed and first
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            # 2. container.xml
            zf.writestr("META-INF/container.xml", container_xml, compress_type=zipfile.ZIP_DEFLATED)
            # 3. CSS
            zf.writestr("OEBPS/styles/style.css", css_content, compress_type=zipfile.ZIP_DEFLATED)
            # 4. Images
            for img_info in images.values():
                zf.writestr(f"OEBPS/images/{img_info['name']}", img_info['data'], compress_type=zipfile.ZIP_DEFLATED)
            # 5. Chapters
            for ch in chapter_files:
                zf.writestr(f"OEBPS/text/{ch['filename']}", ch['content'], compress_type=zipfile.ZIP_DEFLATED)
            # 6. OPF & NCX
            zf.writestr("OEBPS/content.opf", opf_content, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr("OEBPS/toc.ncx", ncx_content, compress_type=zipfile.ZIP_DEFLATED)

        return output_epub_path.is_file() and output_epub_path.stat().st_size > 0
    except Exception as e:
        logger.warning(f"native_fb2_to_epub failed for {fb2_path.name}: {e}")
        return False


def convert_fb2_to_epub(fb2_path: Path, output_epub_path: Path, title: str | None = None, author: str | None = None) -> bool:
    """Convert FB2 to standard EPUB using Calibre's ebook-convert, falling back to native Python converter."""
    if shutil.which("ebook-convert"):
        try:
            env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
            cmd = ["ebook-convert", str(fb2_path), str(output_epub_path)]
            if title:
                cmd.extend(["--title", title])
            if author:
                cmd.extend(["--authors", author])

            # Pre-encode all arguments to raw utf-8 bytes on POSIX to prevent ASCII filesystem encoding errors
            byte_cmd = [a.encode("utf-8") if isinstance(a, str) else bytes(a) for a in cmd]
            subprocess.run(byte_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False, env=env)
            if output_epub_path.is_file() and output_epub_path.stat().st_size > 0:
                return True
        except Exception as e:
            logger.warning(f"Calibre ebook-convert failed: {e}")

    # Seamless fallback to pure-Python native converter
    return native_fb2_to_epub(fb2_path, output_epub_path, title=title, author=author)


def ensure_book_epub_format(db: Session, book_id: int) -> bool:
    """If a book only has FB2/FBZ format, convert it to EPUB and register it in the data table."""
    try:
        formats = db.execute(text("SELECT format, name FROM data WHERE book_id = :bid"), {"bid": book_id}).fetchall()
        fmt_names = {r.format.upper(): r.name for r in formats}
        if "EPUB" in fmt_names:
            return True
        if "FB2" not in fmt_names and "FBZ" not in fmt_names:
            return False

        book_row = db.execute(text("SELECT title, path FROM books WHERE id = :bid"), {"bid": book_id}).fetchone()
        if not book_row or not book_row.path:
            return False

        book_dir = Path(settings.LIBRARY_ROOT) / book_row.path
        if not book_dir.is_dir():
            return False

        fb2_file = None
        for f in book_dir.iterdir():
            if f.is_file() and f.suffix.lower() in ('.fb2', '.fbz'):
                fb2_file = f
                break
        if not fb2_file:
            return False

        base_name = fmt_names.get("FB2") or fmt_names.get("FBZ") or fb2_file.stem
        target_epub = book_dir / f"{base_name}.epub"

        author_row = db.execute(text("""
            SELECT a.name FROM authors a
            JOIN book_authors ba ON a.id = ba.author_id
            WHERE ba.book_id = :bid LIMIT 1
        """), {"bid": book_id}).fetchone()
        author_name = author_row.name if author_row else "Unknown Author"

        if convert_fb2_to_epub(fb2_file, target_epub, title=book_row.title, author=author_name):
            sz = target_epub.stat().st_size
            db.execute(text("DELETE FROM data WHERE book_id = :bid AND UPPER(format) = 'EPUB'"), {"bid": book_id})
            db.execute(text("""
                INSERT INTO data (book_id, format, uncompressed_size, name)
                VALUES (:bid, 'EPUB', :sz, :name)
            """), {"bid": book_id, "sz": sz, "name": base_name})
            db.commit()
            return True
    except Exception as e:
        db.rollback()
        logger.warning(f"Failed to auto-convert book {book_id} to EPUB: {e}")
    return False


def convert_all_fb2_books(db: Session):
    """Background utility to convert all FB2-only books in the catalog to EPUB."""
    try:
        rows = db.execute(text("""
            SELECT DISTINCT b.id FROM books b
            JOIN data d ON b.id = d.book_id
            WHERE d.format IN ('FB2', 'FBZ')
              AND NOT EXISTS (
                  SELECT 1 FROM data d2 WHERE d2.book_id = b.id AND d2.format = 'EPUB'
              )
        """)).fetchall()
        for r in rows:
            ensure_book_epub_format(db, r.id)
    except Exception as e:
        logger.warning(f"Error in convert_all_fb2_books: {e}")


def get_book_metadata(file_path: Path, ext: str) -> dict:
    """Get metadata by combining direct XML parsing with ebook-meta fallback."""
    meta = {}
    if ext in ("fb2", "fbz"):
        meta = parse_fb2_metadata(file_path)
    elif ext == "epub":
        meta = parse_epub_metadata(file_path)

    if (not meta.get("title") or not meta.get("author")) and shutil.which("ebook-meta"):
        try:
            env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
            cmd = ["ebook-meta", str(file_path)]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=60, check=False, env=env)
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

    if not meta.get("cover_bytes") and shutil.which("ebook-meta"):
        temp_cover = Path(tempfile.gettempdir()) / f"cov_{uuid.uuid4().hex}.jpg"
        try:
            env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONIOENCODING": "utf-8"}
            cmd = ["ebook-meta", str(file_path), f"--get-cover={temp_cover}"]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40, check=False, env=env)
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
            try:
                if convert_fb2_to_epub(actual_fb2, target_epub, title=title_hint, author=author_hint):
                    epub_converted_path = target_epub
                    converted_from_fb2 = True
                    epub_meta = parse_epub_metadata(target_epub)
                    if epub_meta.get("cover_bytes") and not meta.get("cover_bytes"):
                        meta["cover_bytes"] = epub_meta["cover_bytes"]
            except Exception as ce:
                logger.warning(f"Error during FB2 to EPUB conversion for {orig_name}: {ce}")
        else:
            meta = get_book_metadata(actual_file_to_process, effective_ext)

        raw_title = (meta.get("title") or "").strip()
        if not raw_title or not re.search(r'[\w\u0400-\u04FF]', raw_title):
            title = Path(orig_name).stem
        else:
            title = raw_title

        raw_author = (meta.get("author") or "").strip()
        if not raw_author or not re.search(r'[\w\u0400-\u04FF]', raw_author):
            primary_author = "Unknown Author"
        else:
            primary_author = raw_author

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
            author_folder = sanitize_filename(primary_author, max_len=64) or "Unknown Author"
            clean_title_part = sanitize_filename(title, max_len=64) or f"Book_{book_id}"
            title_folder = f"{clean_title_part} ({book_id})"
            rel_path = f"{author_folder}/{title_folder}"
            dest_dir = Path(settings.LIBRARY_ROOT) / author_folder / title_folder
            dest_dir.mkdir(parents=True, exist_ok=True)

        base_file_name = sanitize_filename(f"{title} - {primary_author}", max_len=96) or f"book_{book_id}"
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
            # Also preserve the original FB2 format file in the directory
            fb2_dest = dest_dir / f"{base_file_name}.fb2"
            if actual_file_to_process.resolve() != fb2_dest.resolve():
                shutil.copy2(actual_file_to_process, fb2_dest)
            formats_imported.append({
                "format": "FB2",
                "size": fb2_dest.stat().st_size,
                "name": base_file_name
            })
        elif effective_ext in ("fb2", "fbz"):
            fb2_dest = dest_dir / f"{base_file_name}.fb2"
            if actual_file_to_process.resolve() != fb2_dest.resolve():
                shutil.copy2(actual_file_to_process, fb2_dest)
            formats_imported.append({
                "format": "FB2",
                "size": fb2_dest.stat().st_size,
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
                try:
                    db.execute(text("SAVEPOINT sp_lang"))
                    lid = db.execute(text("INSERT INTO languages (lang_code) VALUES (:c) ON CONFLICT (lang_code) DO NOTHING RETURNING id"), {"c": lang_code}).scalar()
                    db.execute(text("RELEASE SAVEPOINT sp_lang"))
                except Exception:
                    db.execute(text("ROLLBACK TO SAVEPOINT sp_lang"))
                    lid = None

                if not lid:
                    l_row2 = db.execute(text("SELECT id FROM languages WHERE lang_code = :c LIMIT 1"), {"c": lang_code}).fetchone()
                    if l_row2:
                        lid = l_row2.id
                    else:
                        max_id = db.execute(text("SELECT COALESCE(MAX(id), 0) + 1 FROM languages")).scalar() or 1
                        lid = db.execute(text("INSERT INTO languages (id, lang_code) VALUES (:lid, :c) RETURNING id"), {"lid": max_id, "c": lang_code}).scalar()
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
