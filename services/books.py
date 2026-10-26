import math
import time
import threading
from sqlalchemy import text


# In-memory thread-safe cache for expensive sidebar and catalog stats
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 600  # 10 minutes
_stats_cache = {"data": None, "expires": 0}
_sidebar_cache = {"data": None, "expires": 0}


def invalidate_caches():
    """Invalidate sidebar and stats cache when library catalog is updated."""
    with _CACHE_LOCK:
        _stats_cache["expires"] = 0
        _sidebar_cache["expires"] = 0


LANGUAGE_NAMES = {
    "rus": "Russian", "ru": "Russian",
    "eng": "English", "en": "English",
    "spa": "Spanish", "es": "Spanish",
    "fra": "French", "fr": "French", "frm": "Middle French",
    "deu": "German", "de": "German",
    "pol": "Polish", "pl": "Polish",
    "ita": "Italian", "it": "Italian",
    "por": "Portuguese", "pt": "Portuguese",
    "ukr": "Ukrainian", "uk": "Ukrainian",
    "bul": "Bulgarian", "bg": "Bulgarian",
    "bel": "Belarusian", "be": "Belarusian",
    "ces": "Czech", "cs": "Czech",
    "nld": "Dutch", "nl": "Dutch",
    "lav": "Latvian", "lv": "Latvian",
    "lit": "Lithuanian", "lt": "Lithuanian",
    "est": "Estonian", "et": "Estonian",
    "hun": "Hungarian", "hu": "Hungarian",
    "ron": "Romanian", "ro": "Romanian",
    "ell": "Greek", "el": "Greek",
    "zho": "Chinese", "zh": "Chinese",
    "jpn": "Japanese", "ja": "Japanese",
    "kor": "Korean", "ko": "Korean",
    "ara": "Arabic", "ar": "Arabic",
    "tur": "Turkish", "tr": "Turkish",
    "swe": "Swedish", "sv": "Swedish",
    "nor": "Norwegian", "no": "Norwegian",
    "dan": "Danish", "da": "Danish",
    "fin": "Finnish", "fi": "Finnish",
    "srp": "Serbian", "sr": "Serbian",
    "hrv": "Croatian", "hr": "Croatian",
    "slk": "Slovak", "sk": "Slovak",
    "slv": "Slovenian", "sl": "Slovenian",
    "heb": "Hebrew", "he": "Hebrew",
    "hin": "Hindi", "hi": "Hindi",
    "ind": "Indonesian", "id": "Indonesian",
    "vie": "Vietnamese", "vi": "Vietnamese",
    "kat": "Georgian", "ka": "Georgian",
    "kaz": "Kazakh", "kk": "Kazakh",
    "uzb": "Uzbek", "uz": "Uzbek",
    "aze": "Azerbaijani", "az": "Azerbaijani",
    "tat": "Tatar", "tt": "Tatar",
    "mon": "Mongolian", "mn": "Mongolian",
    "arm": "Armenian", "hy": "Armenian",
    "epo": "Esperanto", "eo": "Esperanto",
    "lat": "Latin", "la": "Latin",
    "fas": "Persian", "fa": "Persian",
    "isl": "Icelandic", "is": "Icelandic",
    "cat": "Catalan", "ca": "Catalan",
    "eus": "Basque", "eu": "Basque",
    "gle": "Irish", "ga": "Irish",
    "sqi": "Albanian", "sq": "Albanian",
    "mkd": "Macedonian", "mk": "Macedonian",
    "bre": "Breton", "br": "Breton",
    "ang": "Old English",
    "chu": "Old Church Slavonic",
    "hbs": "Serbo-Croatian",
    "chv": "Chuvash",
    "bak": "Bashkir",
    "tgk": "Tajik",
    "abk": "Abkhazian",
    "sme": "Northern Sami",
    "ido": "Ido",
    "asm": "Assamese",
    "que": "Quechua",
    "tuk": "Turkmen",
    "kas": "Kashmiri",
    "ile": "Interlingue",
    "uig": "Uyghur",
}

LANGUAGE_FLAGS = {
    "rus": "🇷🇺", "ru": "🇷🇺",
    "eng": "🇬🇧", "en": "🇬🇧",
    "spa": "🇪🇸", "es": "🇪🇸",
    "fra": "🇫🇷", "fr": "🇫🇷", "frm": "🇫🇷",
    "deu": "🇩🇪", "de": "🇩🇪",
    "pol": "🇵🇱", "pl": "🇵🇱",
    "ita": "🇮🇹", "it": "🇮🇹",
    "por": "🇵🇹", "pt": "🇵🇹",
    "ukr": "🇺🇦", "uk": "🇺🇦",
    "bul": "🇧🇬", "bg": "🇧🇬",
    "bel": "🇧🇾", "be": "🇧🇾",
    "ces": "🇨🇿", "cs": "🇨🇿",
    "nld": "🇳🇱", "nl": "🇳🇱",
    "lav": "🇱🇻", "lv": "🇱🇻",
    "lit": "🇱🇹", "lt": "🇱🇹",
    "est": "🇪🇪", "et": "🇪🇪",
    "hun": "🇭🇺", "hu": "🇭🇺",
    "ron": "🇷🇴", "ro": "🇷🇴",
    "ell": "🇬🇷", "el": "🇬🇷",
    "zho": "🇨🇳", "zh": "🇨🇳",
    "jpn": "🇯🇵", "ja": "🇯🇵",
    "kor": "🇰🇷", "ko": "🇰🇷",
    "ara": "🇸🇦", "ar": "🇸🇦",
    "tur": "🇹🇷", "tr": "🇹🇷",
    "swe": "🇸🇪", "sv": "🇸🇪",
    "nor": "🇳🇴", "no": "🇳🇴",
    "dan": "🇩🇰", "da": "🇩🇰",
    "fin": "🇫🇮", "fi": "🇫🇮",
    "srp": "🇷🇸", "sr": "🇷🇸",
    "hrv": "🇭🇷", "hr": "🇭🇷",
    "slk": "🇸🇰", "sk": "🇸🇰",
    "slv": "🇸🇮", "sl": "🇸🇮",
    "heb": "🇮🇱", "he": "🇮🇱",
    "hin": "🇮🇳", "hi": "🇮🇳",
    "ind": "🇮🇩", "id": "🇮🇩",
    "vie": "🇻🇳", "vi": "🇻🇳",
    "kat": "🇬🇪", "ka": "🇬🇪",
    "kaz": "🇰🇿", "kk": "🇰🇿",
    "uzb": "🇺🇿", "uz": "🇺🇿",
    "aze": "🇦🇿", "az": "🇦🇿",
    "tat": "🇷🇺", "tt": "🇷🇺",
    "mon": "🇲🇳", "mn": "🇲🇳",
    "arm": "🇦🇲", "hy": "🇦🇲",
    "epo": "🌐", "eo": "🌐",
    "lat": "🏛️", "la": "🏛️",
    "fas": "🇮🇷", "fa": "🇮🇷",
    "isl": "🇮🇸", "is": "🇮🇸",
    "cat": "🇪🇸", "ca": "🇪🇸",
    "eus": "🇪🇸", "eu": "🇪🇸",
    "gle": "🇮🇪", "ga": "🇮🇪",
    "sqi": "🇦🇱", "sq": "🇦🇱",
    "mkd": "🇲🇰", "mk": "🇲🇰",
    "bre": "🇫🇷", "br": "🇫🇷",
    "ang": "📜",
    "chu": "⛪",
    "hbs": "🌐",
    "chv": "🇷🇺",
    "bak": "🇷🇺",
    "tgk": "🇹🇯",
    "abk": "🇬🇪",
    "sme": "🌐",
    "ido": "🌐",
    "asm": "🇮🇳",
    "que": "🇵🇪",
    "tuk": "🇹🇲",
    "kas": "🇮🇳",
    "ile": "🌐",
    "uig": "🇨🇳",
}


def format_language_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    clean = code.strip().lower()
    return LANGUAGE_NAMES.get(clean, clean.upper())


def get_language_flag(code: str | None) -> str:
    if not code:
        return "🌐"
    clean = code.strip().lower()
    return LANGUAGE_FLAGS.get(clean, "🌐")


def format_file_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}" if unit != "B" else f"{size_bytes} B"
        size_bytes /= 1024
    return f"{size_bytes:.1f} GB"


def get_stats(db, force_refresh: bool = False):
    now = time.time()
    with _CACHE_LOCK:
        if not force_refresh and _stats_cache["data"] and now < _stats_cache["expires"]:
            return _stats_cache["data"]

    books_count = db.execute(text("SELECT count(*) FROM books")).scalar() or 0
    authors_count = db.execute(text("SELECT count(*) FROM authors")).scalar() or 0
    series_count = db.execute(text("SELECT count(*) FROM series")).scalar() or 0
    tags_count = db.execute(text("SELECT count(*) FROM tags")).scalar() or 0
    data = {
        "books": books_count,
        "authors": authors_count,
        "series": series_count,
        "tags": tags_count,
    }
    with _CACHE_LOCK:
        _stats_cache["data"] = data
        _stats_cache["expires"] = now + _CACHE_TTL_SECONDS
    return data


def toggle_favorite(db, user_id: int, book_id: int) -> bool:
    """Toggle a book favorite for a given user. Returns True if favorited, False if unfavorited."""
    existing = db.execute(
        text("SELECT 1 FROM favorites WHERE user_id = :uid AND book_id = :bid"),
        {"uid": user_id, "bid": book_id}
    ).fetchone()
    if existing:
        db.execute(
            text("DELETE FROM favorites WHERE user_id = :uid AND book_id = :bid"),
            {"uid": user_id, "bid": book_id}
        )
        db.commit()
        return False
    else:
        db.execute(
            text("INSERT INTO favorites (user_id, book_id) VALUES (:uid, :bid) ON CONFLICT DO NOTHING"),
            {"uid": user_id, "bid": book_id}
        )
        db.commit()
        return True


def is_favorite(db, user_id: int, book_id: int) -> bool:
    row = db.execute(
        text("SELECT 1 FROM favorites WHERE user_id = :uid AND book_id = :bid"),
        {"uid": user_id, "bid": book_id}
    ).fetchone()
    return bool(row)


def get_user_favorites_count(db, user_id: int | None) -> int:
    if not user_id:
        return 0
    return db.execute(
        text("SELECT count(*) FROM favorites WHERE user_id = :uid"),
        {"uid": user_id}
    ).scalar() or 0


def get_user_shelf_counts(db, user_id: int | None) -> dict:
    counts = {"reading": 0, "plan_to_read": 0, "finished": 0}
    if not user_id:
        return counts
    rows = db.execute(
        text("""
            SELECT status, count(*) AS count
            FROM reading_status
            WHERE user_id = :uid
            GROUP BY status
        """),
        {"uid": user_id}
    ).fetchall()
    for r in rows:
        if r.status in counts:
            counts[r.status] = r.count
    return counts


def set_reading_status(db, user_id: int, book_id: int, status_val: str | None) -> str | None:
    """Set reading status ('reading', 'plan_to_read', 'finished') or None to clear."""
    if not status_val or status_val in ("none", "clear", ""):
        db.execute(
            text("DELETE FROM reading_status WHERE user_id = :uid AND book_id = :bid"),
            {"uid": user_id, "bid": book_id}
        )
        db.commit()
        return None
    valid_statuses = ("reading", "plan_to_read", "finished")
    clean_status = status_val.strip().lower()
    if clean_status not in valid_statuses:
        return None
    db.execute(
        text("""
            INSERT INTO reading_status (user_id, book_id, status, updated_at)
            VALUES (:uid, :bid, :status, NOW())
            ON CONFLICT (user_id, book_id)
            DO UPDATE SET status = EXCLUDED.status, updated_at = NOW()
        """),
        {"uid": user_id, "bid": book_id, "status": clean_status}
    )
    db.commit()
    return clean_status


def get_reading_status(db, user_id: int | None, book_id: int) -> str | None:
    if not user_id:
        return None
    row = db.execute(
        text("SELECT status FROM reading_status WHERE user_id = :uid AND book_id = :bid"),
        {"uid": user_id, "bid": book_id}
    ).fetchone()
    return row.status if row else None


def save_reading_progress(db, user_id: int, book_id: int, cfi: str, percentage: float) -> dict:
    clamped_pct = max(0.0, min(100.0, float(percentage)))
    db.execute(
        text("""
            INSERT INTO reading_progress (user_id, book_id, cfi, percentage, updated_at)
            VALUES (:uid, :bid, :cfi, :pct, NOW())
            ON CONFLICT (user_id, book_id)
            DO UPDATE SET cfi = EXCLUDED.cfi, percentage = EXCLUDED.percentage, updated_at = NOW()
        """),
        {"uid": user_id, "bid": book_id, "cfi": cfi, "pct": clamped_pct}
    )
    # Automatically mark book as 'reading' if not already shelved or finished
    curr = db.execute(
        text("SELECT status FROM reading_status WHERE user_id = :uid AND book_id = :bid"),
        {"uid": user_id, "bid": book_id}
    ).fetchone()
    if not curr or curr.status == "plan_to_read":
        new_status = "finished" if clamped_pct >= 99.0 else "reading"
        db.execute(
            text("""
                INSERT INTO reading_status (user_id, book_id, status, updated_at)
                VALUES (:uid, :bid, :st, NOW())
                ON CONFLICT (user_id, book_id)
                DO UPDATE SET status = EXCLUDED.status, updated_at = NOW()
            """),
            {"uid": user_id, "bid": book_id, "st": new_status}
        )
    elif clamped_pct >= 99.0 and curr.status != "finished":
        db.execute(
            text("UPDATE reading_status SET status = 'finished', updated_at = NOW() WHERE user_id = :uid AND book_id = :bid"),
            {"uid": user_id, "bid": book_id}
        )
    db.commit()
    return {"cfi": cfi, "percentage": clamped_pct}


def get_reading_progress(db, user_id: int | None, book_id: int) -> dict | None:
    if not user_id:
        return None
    row = db.execute(
        text("SELECT cfi, percentage, updated_at FROM reading_progress WHERE user_id = :uid AND book_id = :bid"),
        {"uid": user_id, "bid": book_id}
    ).fetchone()
    if not row:
        return None
    return {
        "cfi": row.cfi,
        "percentage": round(row.percentage, 1),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None
    }


def get_sidebar_data(db, force_refresh: bool = False):
    now = time.time()
    with _CACHE_LOCK:
        if not force_refresh and _sidebar_cache["data"] and now < _sidebar_cache["expires"]:
            return _sidebar_cache["data"]

    top_tags = db.execute(text("""
        SELECT t.id, t.name, count(*) AS count
        FROM tags t
        JOIN book_tags bt ON t.id = bt.tag_id
        GROUP BY t.id, t.name
        ORDER BY count DESC
        LIMIT 20
    """)).fetchall()

    top_series = db.execute(text("""
        SELECT s.id, s.name, count(*) AS count
        FROM series s
        JOIN book_series bs ON s.id = bs.series_id
        GROUP BY s.id, s.name
        ORDER BY count DESC
        LIMIT 20
    """)).fetchall()

    formats = db.execute(text("""
        SELECT format, count(*) AS count
        FROM data
        GROUP BY format
        ORDER BY count DESC
        LIMIT 10
    """)).fetchall()

    top_languages = db.execute(text("""
        SELECT l.id, l.lang_code, count(*) AS count
        FROM languages l
        JOIN book_languages bl ON l.id = bl.language_id
        GROUP BY l.id, l.lang_code
        ORDER BY count DESC
        LIMIT 50
    """)).fetchall()

    data = {
        "tags": [{"id": r.id, "name": r.name, "count": r.count} for r in top_tags],
        "series": [{"id": r.id, "name": r.name, "count": r.count} for r in top_series],
        "formats": [{"format": r.format, "count": r.count} for r in formats],
        "languages": [
            {
                "id": r.id,
                "code": r.lang_code.upper() if r.lang_code else "",
                "name": format_language_name(r.lang_code),
                "flag": get_language_flag(r.lang_code),
                "count": r.count
            }
            for r in top_languages
        ],
    }
    with _CACHE_LOCK:
        _sidebar_cache["data"] = data
        _sidebar_cache["expires"] = now + _CACHE_TTL_SECONDS
    return data



def group_books_by_author_and_series(books: list[dict], query: str | None = None) -> list[dict]:
    """
    Groups books into a structured author and series hierarchy for clean visual presentation.
    """
    if not books:
        return []

    q_lower = query.strip().lower() if query and query.strip() else None
    author_map = {}

    for b in books:
        a_key = b.get("author_id") or b.get("author") or "unknown"
        if a_key not in author_map:
            author_name = b.get("author") or "Unknown Author"
            author_sort = b.get("author_sort") or author_name
            parts = [p[0].upper() for p in author_name.replace(',', ' ').split() if p and p[0].isalnum()]
            initials = "".join(parts[:2]) if parts else "??"
            name_match = bool(q_lower and (q_lower in author_name.lower() or q_lower in author_sort.lower()))

            author_map[a_key] = {
                "author_id": b.get("author_id"),
                "author_name": author_name,
                "author_sort": author_sort,
                "initials": initials,
                "name_match": name_match,
                "total_books": 0,
                "series_dict": {},
                "standalone_books": [],
            }

        entry = author_map[a_key]
        entry["total_books"] += 1

        if b.get("series_name"):
            sid = b.get("series_id") or b.get("series_name")
            if sid not in entry["series_dict"]:
                entry["series_dict"][sid] = {
                    "series_id": b.get("series_id"),
                    "series_name": b.get("series_name"),
                    "books": [],
                }
            entry["series_dict"][sid]["books"].append(b)
        else:
            entry["standalone_books"].append(b)

    result = []
    for entry in author_map.values():
        series_groups = []
        for s in entry["series_dict"].values():
            s["books"].sort(key=lambda x: (x.get("series_index_num", 999999), x.get("title", "")))
            series_groups.append(s)
        series_groups.sort(key=lambda s: s["series_name"].lower())
        entry["series_groups"] = series_groups
        entry["series_count"] = len(series_groups)
        del entry["series_dict"]
        result.append(entry)

    if q_lower:
        def author_rank(a):
            match = a["name_match"]
            count = a["total_books"]
            if match and count > 1:
                tier = 0
            elif count > 1:
                tier = 1
            elif match:
                tier = 2
            else:
                tier = 3
            return (tier, -count, a["author_name"])

        result.sort(key=author_rank)
    else:
        result.sort(key=lambda a: (-a["total_books"], a["author_name"]))

    return result


def get_books(
    db,
    page: int = 1,
    size: int = 36,
    q: str | None = None,
    author_id: int | None = None,
    series_id: int | None = None,
    tag_id: int | None = None,
    format_name: str | None = None,
    language_id: int | None = None,
    sort_by: str = "recent",
    favorites_only: bool = False,
    reading_status: str | None = None,
    search_descriptions: bool = False,
    user_id: int | None = None,
    view_mode: str | None = None,
):
    page = max(1, page)
    size = min(max(12, size), 72)
    offset = (page - 1) * size

    # Determine view mode: explicit or auto (search defaults to grouped)
    if view_mode in ("grouped", "grid"):
        effective_view_mode = view_mode
    elif q and q.strip():
        effective_view_mode = "grouped"
    else:
        effective_view_mode = "grid"

    where_clauses = []
    params = {"limit": size, "offset": offset}

    # Favorites filter
    if favorites_only and user_id:
        params["fav_user_id"] = user_id
        where_clauses.append("b.id IN (SELECT book_id FROM favorites WHERE user_id = :fav_user_id)")

    # Reading status shelf filter
    if reading_status and user_id:
        params["rs_user_id"] = user_id
        params["rs_status"] = reading_status.strip().lower()
        where_clauses.append("b.id IN (SELECT book_id FROM reading_status WHERE user_id = :rs_user_id AND status = :rs_status)")

    # Search query
    if q and q.strip():
        q_clean = q.strip()
        words = q_clean.split()
        if len(words) == 1:
            params["q_pattern"] = f"%{words[0]}%"
            if search_descriptions:
                where_clauses.append("(b.title ILIKE :q_pattern OR b.author_sort ILIKE :q_pattern OR EXISTS (SELECT 1 FROM comments c WHERE c.book_id = b.id AND c.text ILIKE :q_pattern))")
            else:
                where_clauses.append("(b.title ILIKE :q_pattern OR b.author_sort ILIKE :q_pattern)")
        else:
            word_clauses = []
            for idx, w in enumerate(words[:5]):
                pkey = f"w_{idx}"
                params[pkey] = f"%{w}%"
                if search_descriptions:
                    word_clauses.append(f"(b.title ILIKE :{pkey} OR b.author_sort ILIKE :{pkey} OR EXISTS (SELECT 1 FROM comments c WHERE c.book_id = b.id AND c.text ILIKE :{pkey}))")
                else:
                    word_clauses.append(f"(b.title ILIKE :{pkey} OR b.author_sort ILIKE :{pkey})")
            where_clauses.append("(" + " AND ".join(word_clauses) + ")")

    # Filters
    if author_id:
        params["author_id"] = author_id
        where_clauses.append("EXISTS (SELECT 1 FROM book_authors ba WHERE ba.book_id = b.id AND ba.author_id = :author_id)")

    if series_id:
        params["series_id"] = series_id
        where_clauses.append("EXISTS (SELECT 1 FROM book_series bs WHERE bs.book_id = b.id AND bs.series_id = :series_id)")

    if tag_id:
        params["tag_id"] = tag_id
        where_clauses.append("EXISTS (SELECT 1 FROM book_tags bt WHERE bt.book_id = b.id AND bt.tag_id = :tag_id)")

    if format_name:
        params["format_name"] = format_name.upper()
        where_clauses.append("EXISTS (SELECT 1 FROM data d WHERE d.book_id = b.id AND UPPER(d.format) = :format_name)")

    if language_id:
        params["language_id"] = language_id
        where_clauses.append("EXISTS (SELECT 1 FROM book_languages bl WHERE bl.book_id = b.id AND bl.language_id = :language_id)")

    where_str = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # Sort order
    if sort_by == "title_asc":
        order_str = "b.title ASC, b.id DESC"
    elif sort_by == "title_desc":
        order_str = "b.title DESC, b.id DESC"
    elif sort_by == "author_asc":
        order_str = "b.author_sort ASC, b.title ASC"
    elif sort_by == "pubdate_desc":
        order_str = "b.pubdate DESC NULLS LAST, b.id DESC"
    else:  # default "recent"
        order_str = "b.id DESC"

    # Total Count query
    if not where_clauses:
        total = db.execute(text("SELECT count(*) FROM books")).scalar() or 0
    elif favorites_only and user_id and len(where_clauses) == 1:
        total = db.execute(text("SELECT count(*) FROM favorites WHERE user_id = :fav_user_id"), {"fav_user_id": user_id}).scalar() or 0
    elif author_id and len(where_clauses) == 1:
        total = db.execute(text("SELECT count(*) FROM book_authors WHERE author_id = :author_id"), {"author_id": author_id}).scalar() or 0
    elif series_id and len(where_clauses) == 1:
        total = db.execute(text("SELECT count(*) FROM book_series WHERE series_id = :series_id"), {"series_id": series_id}).scalar() or 0
    elif tag_id and len(where_clauses) == 1:
        total = db.execute(text("SELECT count(*) FROM book_tags WHERE tag_id = :tag_id"), {"tag_id": tag_id}).scalar() or 0
    elif language_id and len(where_clauses) == 1:
        total = db.execute(text("SELECT count(*) FROM book_languages WHERE language_id = :language_id"), {"language_id": language_id}).scalar() or 0
    else:
        count_sql = text(f"SELECT count(*) FROM books b {where_str}")
        total = db.execute(count_sql, params).scalar() or 0

    total_pages = max(1, math.ceil(total / size))
    page = min(page, total_pages)

    # Main query using CTE for instant pagination
    query_sql = text(f"""
        WITH page_books AS (
            SELECT
                b.id,
                b.title,
                b.author_sort,
                b.has_cover,
                b.series_index,
                b.pubdate
            FROM books b
            {where_str}
            ORDER BY {order_str}
            LIMIT :limit OFFSET :offset
        )
        SELECT
            pb.id,
            pb.title,
            pb.author_sort,
            pb.has_cover,
            pb.series_index,
            s.id AS series_id,
            s.name AS series_name,
            auth.author_id,
            auth.author_name,
            lng.lang_code,
            COALESCE(
                json_agg(
                    json_build_object(
                        'format', d.format,
                        'size', d.uncompressed_size,
                        'name', d.name
                    )
                ) FILTER (WHERE d.id IS NOT NULL),
                '[]'::json
            ) AS formats
        FROM page_books pb
        LEFT JOIN book_series bs ON pb.id = bs.book_id
        LEFT JOIN series s ON bs.series_id = s.id
        LEFT JOIN data d ON pb.id = d.book_id
        LEFT JOIN LATERAL (
            SELECT ba.author_id, a.name AS author_name
            FROM book_authors ba
            JOIN authors a ON ba.author_id = a.id
            WHERE ba.book_id = pb.id
            ORDER BY ba.author_id
            LIMIT 1
        ) auth ON true
        LEFT JOIN LATERAL (
            SELECT l.lang_code
            FROM book_languages bl
            JOIN languages l ON bl.language_id = l.id
            WHERE bl.book_id = pb.id
            LIMIT 1
        ) lng ON true
        GROUP BY pb.id, pb.title, pb.author_sort, pb.has_cover, pb.series_index, s.id, s.name, auth.author_id, auth.author_name, pb.pubdate, lng.lang_code
        ORDER BY {order_str.replace('b.', 'pb.')}
    """)

    rows = db.execute(query_sql, params).fetchall()

    fav_set = set()
    prog_map = {}
    shelf_map = {}
    if user_id and rows:
        book_ids = [r.id for r in rows]
        fav_rows = db.execute(
            text("SELECT book_id FROM favorites WHERE user_id = :uid AND book_id = ANY(:bids)"),
            {"uid": user_id, "bids": book_ids}
        ).fetchall()
        fav_set = {r.book_id for r in fav_rows}

        prog_rows = db.execute(
            text("SELECT book_id, percentage FROM reading_progress WHERE user_id = :uid AND book_id = ANY(:bids)"),
            {"uid": user_id, "bids": book_ids}
        ).fetchall()
        prog_map = {p.book_id: round(p.percentage, 1) for p in prog_rows}

        shelf_rows = db.execute(
            text("SELECT book_id, status FROM reading_status WHERE user_id = :uid AND book_id = ANY(:bids)"),
            {"uid": user_id, "bids": book_ids}
        ).fetchall()
        shelf_map = {s.book_id: s.status for s in shelf_rows}

    books = []
    for r in rows:
        book_formats = []
        for fmt_info in r.formats:
            fmt = fmt_info.get("format")
            sz = fmt_info.get("size")
            book_formats.append({
                "format": fmt,
                "size_formatted": format_file_size(sz),
                "download_url": f"/download/{r.id}/{fmt.lower()}"
            })

        s_idx = r.series_index
        s_idx_num = float(s_idx) if s_idx is not None else 999999
        s_idx_str = f"#{int(s_idx)}" if s_idx and s_idx == int(s_idx) else (f"#{s_idx:.1f}" if s_idx else None)
        author_display = r.author_name or r.author_sort or "Unknown Author"

        lang_code = r.lang_code.upper() if r.lang_code else None
        lang_name = format_language_name(r.lang_code) if r.lang_code else None
        lang_flag = get_language_flag(r.lang_code) if r.lang_code else None

        books.append({
            "id": r.id,
            "title": r.title,
            "author": author_display,
            "author_sort": r.author_sort or author_display,
            "author_id": r.author_id,
            "has_cover": bool(r.has_cover),
            "cover_url": f"/cover/{r.id}",
            "thumbnail_url": f"/thumbnail/{r.id}",
            "series_id": r.series_id,
            "series_name": r.series_name,
            "series_index": s_idx_str,
            "series_index_num": s_idx_num,
            "language_code": lang_code,
            "language_name": lang_name,
            "language_flag": lang_flag,
            "formats": book_formats,
            "is_favorite": (r.id in fav_set),
            "reading_percentage": prog_map.get(r.id),
            "reading_status": shelf_map.get(r.id),
        })

    grouped_authors = group_books_by_author_and_series(books, q) if effective_view_mode == "grouped" else []

    return {
        "books": books,
        "grouped_authors": grouped_authors,
        "view_mode": effective_view_mode,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "size": size,
    }


def get_book_details(db, book_id: int, user_id: int | None = None):
    # Book core info
    row = db.execute(text("""
        SELECT
            b.id,
            b.title,
            b.author_sort,
            b.has_cover,
            b.series_index,
            b.pubdate,
            b.isbn,
            b.path,
            s.id AS series_id,
            s.name AS series_name,
            p.name AS publisher_name,
            c.text AS comment_text
        FROM books b
        LEFT JOIN book_series bs ON b.id = bs.book_id
        LEFT JOIN series s ON bs.series_id = s.id
        LEFT JOIN book_publishers bp ON b.id = bp.book_id
        LEFT JOIN publishers p ON bp.publisher_id = p.id
        LEFT JOIN comments c ON b.id = c.book_id
        WHERE b.id = :id
    """), {"id": book_id}).fetchone()

    if not row:
        return None

    # Authors
    authors_rows = db.execute(text("""
        SELECT a.id, a.name
        FROM authors a
        JOIN book_authors ba ON a.id = ba.author_id
        WHERE ba.book_id = :id
    """), {"id": book_id}).fetchall()
    authors = [{"id": a.id, "name": a.name} for a in authors_rows]

    # Tags
    tags_rows = db.execute(text("""
        SELECT t.id, t.name
        FROM tags t
        JOIN book_tags bt ON t.id = bt.tag_id
        WHERE bt.book_id = :id
        ORDER BY t.name
    """), {"id": book_id}).fetchall()
    tags = [{"id": t.id, "name": t.name} for t in tags_rows]

    # Identifiers (ISBN, etc.)
    ident_rows = db.execute(text("""
        SELECT type, val
        FROM identifiers
        WHERE book_id = :id
    """), {"id": book_id}).fetchall()
    identifiers = [{"type": i.type, "val": i.val} for i in ident_rows]

    # Languages
    lang_rows = db.execute(text("""
        SELECT l.id, l.lang_code
        FROM languages l
        JOIN book_languages bl ON l.id = bl.language_id
        WHERE bl.book_id = :id
    """), {"id": book_id}).fetchall()
    languages = [
        {
            "id": l.id,
            "code": l.lang_code.upper() if l.lang_code else "",
            "name": format_language_name(l.lang_code),
            "flag": get_language_flag(l.lang_code)
        }
        for l in lang_rows
    ]
    primary_lang_code = languages[0]["code"] if languages else None
    primary_lang_name = languages[0]["name"] if languages else None
    primary_lang_flag = languages[0]["flag"] if languages else None

    # Formats
    formats_rows = db.execute(text("""
        SELECT format, uncompressed_size, name
        FROM data
        WHERE book_id = :id
        ORDER BY format
    """), {"id": book_id}).fetchall()
    formats = [
        {
            "format": f.format,
            "size_formatted": format_file_size(f.uncompressed_size),
            "size_bytes": f.uncompressed_size,
            "filename": f"{f.name}.{f.format.lower()}",
            "download_url": f"/download/{book_id}/{f.format.lower()}"
        }
        for f in formats_rows
    ]

    pubdate_str = None
    if row.pubdate:
        pubdate_str = row.pubdate.strftime("%Y-%m-%d")

    is_fav = False
    reading_prog = None
    reading_stat = None
    if user_id:
        is_fav = is_favorite(db, user_id, book_id)
        reading_prog = get_reading_progress(db, user_id, book_id)
        reading_stat = get_reading_status(db, user_id, book_id)

    # 1. Full Series Reading Order & Quick Prev/Next Navigation
    series_books = []
    prev_series_book = None
    next_series_book = None
    if row.series_id:
        sb_rows = db.execute(text("""
            SELECT b.id, b.title, b.series_index, b.has_cover
            FROM book_series bs
            JOIN books b ON bs.book_id = b.id
            WHERE bs.series_id = :sid
            ORDER BY b.series_index ASC NULLS LAST, b.id ASC
        """), {"sid": row.series_id}).fetchall()

        for idx, sb in enumerate(sb_rows):
            s_idx_str = f"#{int(sb.series_index)}" if sb.series_index and sb.series_index == int(sb.series_index) else (f"#{sb.series_index:.1f}" if sb.series_index else None)
            series_books.append({
                "id": sb.id,
                "title": sb.title,
                "series_index": s_idx_str,
                "has_cover": bool(sb.has_cover),
                "thumbnail_url": f"/thumbnail/{sb.id}",
                "is_current": (sb.id == book_id),
            })

        for idx, sb in enumerate(series_books):
            if sb["is_current"]:
                if idx > 0:
                    prev_series_book = series_books[idx - 1]
                if idx < len(series_books) - 1:
                    next_series_book = series_books[idx + 1]
                break

    # 2. "More by this Author" Recommendations
    more_by_author = []
    if authors:
        primary_aid = authors[0]["id"]
        more_rows = db.execute(text("""
            SELECT b.id, b.title, b.has_cover, b.series_index, s.name AS series_name
            FROM book_authors ba
            JOIN books b ON ba.book_id = b.id
            LEFT JOIN book_series bs ON b.id = bs.book_id
            LEFT JOIN series s ON bs.series_id = s.id
            WHERE ba.author_id = :aid AND b.id != :bid
            ORDER BY b.pubdate DESC NULLS LAST, b.id DESC
            LIMIT 6
        """), {"aid": primary_aid, "bid": book_id}).fetchall()

        for mb in more_rows:
            more_by_author.append({
                "id": mb.id,
                "title": mb.title,
                "has_cover": bool(mb.has_cover),
                "thumbnail_url": f"/thumbnail/{mb.id}",
                "series_name": mb.series_name,
                "series_index": f"#{int(mb.series_index)}" if mb.series_index and mb.series_index == int(mb.series_index) else None,
            })

    return {
        "id": row.id,
        "title": row.title,
        "author": row.author_sort or (authors[0]["name"] if authors else "Unknown Author"),
        "authors": authors,
        "has_cover": bool(row.has_cover),
        "cover_url": f"/cover/{row.id}",
        "thumbnail_url": f"/thumbnail/{row.id}",
        "series_id": row.series_id,
        "series_name": row.series_name,
        "series_index": f"#{int(row.series_index)}" if row.series_index and row.series_index == int(row.series_index) else (f"#{row.series_index:.1f}" if row.series_index else None),
        "series_books": series_books,
        "prev_series_book": prev_series_book,
        "next_series_book": next_series_book,
        "more_by_author": more_by_author,
        "publisher": row.publisher_name,
        "pubdate": pubdate_str,
        "language_code": primary_lang_code,
        "language_name": primary_lang_name,
        "language_flag": primary_lang_flag,
        "languages": languages,
        "description": row.comment_text,
        "tags": tags,
        "identifiers": identifiers,
        "formats": formats,
        "path": row.path,
        "is_favorite": is_fav,
        "reading_progress": reading_prog,
        "reading_status": reading_stat,
    }


# ===================================================================
# READER NOTES, BOOKMARKS & USER READING ACTIVITY
# ===================================================================

def get_user_notes(db, user_id: int, book_id: int) -> list[dict]:
    """Retrieve all highlights and notes created by the user for a book."""
    rows = db.execute(text("""
        SELECT id, book_id, cfi, text, note, color, created_at
        FROM user_notes
        WHERE user_id = :uid AND book_id = :bid
        ORDER BY created_at ASC
    """), {"uid": user_id, "bid": book_id}).fetchall()
    return [
        {
            "id": r.id,
            "book_id": r.book_id,
            "cfi": r.cfi,
            "text": r.text,
            "note": r.note or "",
            "color": r.color or "yellow",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""
        }
        for r in rows
    ]


def add_user_note(db, user_id: int, book_id: int, cfi: str, text_content: str, note: str = "", color: str = "yellow") -> dict:
    """Save an ebook highlight/note annotation."""
    row = db.execute(text("""
        INSERT INTO user_notes (user_id, book_id, cfi, text, note, color, created_at)
        VALUES (:uid, :bid, :cfi, :txt, :note, :color, NOW())
        RETURNING id, created_at
    """), {
        "uid": user_id,
        "bid": book_id,
        "cfi": cfi,
        "txt": text_content,
        "note": note,
        "color": color
    }).fetchone()
    db.commit()
    return {
        "id": row.id,
        "book_id": book_id,
        "cfi": cfi,
        "text": text_content,
        "note": note,
        "color": color,
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else ""
    }


def delete_user_note(db, user_id: int, note_id: int) -> bool:
    """Delete a note/highlight belonging to the user."""
    res = db.execute(text("""
        DELETE FROM user_notes WHERE id = :nid AND user_id = :uid
    """), {"nid": note_id, "uid": user_id})
    db.commit()
    return res.rowcount > 0


def get_user_bookmarks(db, user_id: int, book_id: int) -> list[dict]:
    """Retrieve bookmarks for a book."""
    rows = db.execute(text("""
        SELECT id, book_id, cfi, label, percentage, created_at
        FROM user_bookmarks
        WHERE user_id = :uid AND book_id = :bid
        ORDER BY percentage ASC, created_at ASC
    """), {"uid": user_id, "bid": book_id}).fetchall()
    return [
        {
            "id": r.id,
            "book_id": r.book_id,
            "cfi": r.cfi,
            "label": r.label or f"Bookmark at {r.percentage}%",
            "percentage": r.percentage or 0,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""
        }
        for r in rows
    ]


def add_user_bookmark(db, user_id: int, book_id: int, cfi: str, label: str = "", percentage: int = 0) -> dict:
    """Save a reader bookmark."""
    clean_label = label.strip() if label and label.strip() else f"Page {percentage}%"
    row = db.execute(text("""
        INSERT INTO user_bookmarks (user_id, book_id, cfi, label, percentage, created_at)
        VALUES (:uid, :bid, :cfi, :label, :pct, NOW())
        RETURNING id, created_at
    """), {
        "uid": user_id,
        "bid": book_id,
        "cfi": cfi,
        "label": clean_label,
        "pct": int(percentage)
    }).fetchone()
    db.commit()
    return {
        "id": row.id,
        "book_id": book_id,
        "cfi": cfi,
        "label": clean_label,
        "percentage": int(percentage),
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else ""
    }


def delete_user_bookmark(db, user_id: int, bookmark_id: int) -> bool:
    """Delete a bookmark."""
    res = db.execute(text("""
        DELETE FROM user_bookmarks WHERE id = :bmid AND user_id = :uid
    """), {"bmid": bookmark_id, "uid": user_id})
    db.commit()
    return res.rowcount > 0


def get_user_reading_stats(db, user_id: int) -> dict:
    """Detailed reading statistics and activity for the user profile."""
    shelf_counts = get_user_shelf_counts(db, user_id)
    fav_count = get_user_favorites_count(db, user_id)
    notes_count = db.execute(text("SELECT count(*) FROM user_notes WHERE user_id = :uid"), {"uid": user_id}).scalar() or 0
    bookmarks_count = db.execute(text("SELECT count(*) FROM user_bookmarks WHERE user_id = :uid"), {"uid": user_id}).scalar() or 0

    # Currently reading books with progress
    in_progress = db.execute(text("""
        SELECT b.id, b.title, b.author_sort, b.has_cover, rp.percentage, rp.updated_at
        FROM reading_progress rp
        JOIN books b ON rp.book_id = b.id
        WHERE rp.user_id = :uid AND rp.percentage > 0 AND rp.percentage < 99
        ORDER BY rp.updated_at DESC
        LIMIT 6
    """), {"uid": user_id}).fetchall()

    in_progress_books = [
        {
            "id": r.id,
            "title": r.title,
            "author": r.author_sort or "Unknown",
            "has_cover": bool(r.has_cover),
            "thumbnail_url": f"/thumbnail/{r.id}",
            "percentage": round(r.percentage, 1),
            "last_read": r.updated_at.strftime("%b %d, %Y") if r.updated_at else "Recently"
        }
        for r in in_progress
    ]

    # Finished books
    finished = db.execute(text("""
        SELECT b.id, b.title, b.author_sort, b.has_cover, rs.updated_at
        FROM reading_status rs
        JOIN books b ON rs.book_id = b.id
        WHERE rs.user_id = :uid AND rs.status = 'finished'
        ORDER BY rs.updated_at DESC
        LIMIT 6
    """), {"uid": user_id}).fetchall()

    finished_books = [
        {
            "id": r.id,
            "title": r.title,
            "author": r.author_sort or "Unknown",
            "has_cover": bool(r.has_cover),
            "thumbnail_url": f"/thumbnail/{r.id}",
            "finished_at": r.updated_at.strftime("%b %d, %Y") if r.updated_at else ""
        }
        for r in finished
    ]

    # Top genres
    top_genres = db.execute(text("""
        SELECT t.name, count(*) as count
        FROM (
            SELECT book_id FROM reading_status WHERE user_id = :uid
            UNION
            SELECT book_id FROM reading_progress WHERE user_id = :uid
            UNION
            SELECT book_id FROM favorites WHERE user_id = :uid
        ) ub
        JOIN book_tags bt ON ub.book_id = bt.book_id
        JOIN tags t ON bt.tag_id = t.id
        GROUP BY t.name
        ORDER BY count DESC
        LIMIT 5
    """), {"uid": user_id}).fetchall()
    genres_list = [{"name": r.name, "count": r.count} for r in top_genres]

    return {
        "shelf_counts": shelf_counts,
        "favorites_count": fav_count,
        "notes_count": notes_count,
        "bookmarks_count": bookmarks_count,
        "in_progress_books": in_progress_books,
        "finished_books": finished_books,
        "top_genres": genres_list,
    }

