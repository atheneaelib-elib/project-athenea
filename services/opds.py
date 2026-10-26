import xml.sax.saxutils as saxutils
from datetime import datetime, timezone
from pathlib import Path


OPDS_MIME_NAVIGATION = "application/atom+xml;profile=opds-catalog;kind=navigation;charset=utf-8"
OPDS_MIME_ACQUISITION = "application/atom+xml;profile=opds-catalog;kind=acquisition;charset=utf-8"
OPENSEARCH_MIME = "application/opensearchdescription+xml;charset=utf-8"

MIME_TYPES = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "mobi": "application/x-mobipocket-ebook",
    "azw3": "application/vnd.amazon.ebook",
    "fb2": "application/x-fictionbook+xml",
    "fb2.zip": "application/x-zip-compressed-fb2",
    "txt": "text/plain;charset=utf-8",
    "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar",
    "djvu": "image/vnd.djvu",
}


def _escape(text: str | None) -> str:
    if not text:
        return ""
    return saxutils.escape(str(text), {'"': "&quot;", "'": "&apos;"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_opensearch_xml(base_url: str = "") -> str:
    search_url = f"{base_url}/opds/search?q={{searchTerms}}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>Athenea</ShortName>
  <Description>Search Athenea by Title, Author, or Keywords</Description>
  <InputEncoding>UTF-8</InputEncoding>
  <OutputEncoding>UTF-8</OutputEncoding>
  <Url type="application/atom+xml;profile=opds-catalog;kind=acquisition" template="{_escape(search_url)}" />
</OpenSearchDescription>"""


def generate_root_catalog(base_url: str = "", stats: dict | None = None) -> str:
    updated = _now_iso()
    books_count = stats.get("books", 0) if stats else 0
    authors_count = stats.get("authors", 0) if stats else 0
    series_count = stats.get("series", 0) if stats else 0
    tags_count = stats.get("tags", 0) if stats else 0

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:opds:root</id>
  <title>Athenea</title>
  <subtitle>Over {books_count:,} books catalog</subtitle>
  <updated>{updated}</updated>
  <author>
    <name>Athenea</name>
  </author>
  <link rel="self" href="{base_url}/opds" type="{OPDS_MIME_NAVIGATION}" />
  <link rel="start" href="{base_url}/opds" type="{OPDS_MIME_NAVIGATION}" />
  <link rel="search" href="{base_url}/opds/opensearch.xml" type="{OPENSEARCH_MIME}" title="Search Athenea" />

  <entry>
    <title>Recently Added Books</title>
    <id>urn:opds:recent</id>
    <updated>{updated}</updated>
    <content type="text">Browse the most recently added books to the library</content>
    <link rel="subsection" href="{base_url}/opds/recent" type="{OPDS_MIME_ACQUISITION}" />
  </entry>

  <entry>
    <title>Authors ({authors_count:,})</title>
    <id>urn:opds:authors</id>
    <updated>{updated}</updated>
    <content type="text">Browse books organized by author</content>
    <link rel="subsection" href="{base_url}/opds/authors" type="{OPDS_MIME_NAVIGATION}" />
  </entry>

  <entry>
    <title>Series ({series_count:,})</title>
    <id>urn:opds:series</id>
    <updated>{updated}</updated>
    <content type="text">Browse book collections and series in reading order</content>
    <link rel="subsection" href="{base_url}/opds/series" type="{OPDS_MIME_NAVIGATION}" />
  </entry>

  <entry>
    <title>Genres &amp; Tags ({tags_count:,})</title>
    <id>urn:opds:genres</id>
    <updated>{updated}</updated>
    <content type="text">Explore books categorized by genre and subject</content>
    <link rel="subsection" href="{base_url}/opds/genres" type="{OPDS_MIME_NAVIGATION}" />
  </entry>

  <entry>
    <title>Languages</title>
    <id>urn:opds:languages</id>
    <updated>{updated}</updated>
    <content type="text">Filter books by written language</content>
    <link rel="subsection" href="{base_url}/opds/languages" type="{OPDS_MIME_NAVIGATION}" />
  </entry>
</feed>"""


def generate_acquisition_feed(
    title: str,
    feed_id: str,
    self_url: str,
    books: list[dict],
    page: int = 1,
    total_pages: int = 1,
    base_url: str = "",
    next_url: str | None = None,
    prev_url: str | None = None,
    search_url: str | None = None,
) -> str:
    updated = _now_iso()
    entries_xml = []

    for b in books:
        bid = b["id"]
        btitle = _escape(b.get("title") or "Untitled")
        author = _escape(b.get("author") or "Unknown Author")
        desc = _escape(b.get("description") or f"Book #{bid}: {btitle}")
        lang = b.get("language_code") or ""
        series_str = ""
        if b.get("series_name"):
            s_name = _escape(b.get("series_name"))
            s_idx = b.get("series_index") or ""
            series_str = f"""<category term="{s_name}" label="Series: {s_name} {s_idx}" />"""

        # Cover links
        cover_url = f"{base_url}/cover/{bid}"
        thumb_url = f"{base_url}/thumbnail/{bid}"
        images_xml = f"""
    <link rel="http://opds-spec.org/image" href="{cover_url}" type="image/jpeg" />
    <link rel="http://opds-spec.org/image/thumbnail" href="{thumb_url}" type="image/webp" />"""

        # Acquisition download links
        acq_links = []
        for fmt in b.get("formats", []):
            fmt_name = fmt.get("format", "").upper()
            fmt_lower = fmt_name.lower()
            mime = MIME_TYPES.get(fmt_lower, "application/octet-stream")
            dl_url = f"{base_url}/download/{bid}/{fmt_lower}"
            acq_links.append(
                f"""    <link rel="http://opds-spec.org/acquisition" href="{dl_url}" type="{mime}" title="{fmt_name}" />"""
            )

        acq_str = "\n".join(acq_links)
        lang_tag = f"<dc:language>{lang.lower()}</dc:language>" if lang else ""

        entries_xml.append(f"""  <entry>
    <title>{btitle}</title>
    <id>urn:book:{bid}</id>
    <updated>{updated}</updated>
    <author><name>{author}</name></author>
    <summary type="text">{desc}</summary>
    {lang_tag}
    {series_str}
{images_xml}
{acq_str}
  </entry>""")

    pagination_links = [
        f"""  <link rel="self" href="{self_url}" type="{OPDS_MIME_ACQUISITION}" />""",
        f"""  <link rel="start" href="{base_url}/opds" type="{OPDS_MIME_NAVIGATION}" />""",
        f"""  <link rel="search" href="{base_url}/opds/opensearch.xml" type="{OPENSEARCH_MIME}" />""",
    ]
    if next_url:
        pagination_links.append(f"""  <link rel="next" href="{next_url}" type="{OPDS_MIME_ACQUISITION}" />""")
    if prev_url:
        pagination_links.append(f"""  <link rel="previous" href="{prev_url}" type="{OPDS_MIME_ACQUISITION}" />""")

    links_str = "\n".join(pagination_links)
    all_entries = "\n".join(entries_xml)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>{feed_id}</id>
  <title>{_escape(title)} (Page {page} of {total_pages})</title>
  <updated>{updated}</updated>
{links_str}
{all_entries}
</feed>"""


def generate_navigation_list_feed(
    title: str,
    feed_id: str,
    self_url: str,
    items: list[dict],
    base_url: str = "",
) -> str:
    """Generic navigation feed generator for lists of authors, series, genres, etc."""
    updated = _now_iso()
    entries_xml = []

    for item in items:
        iid = item["id"]
        iname = _escape(item["name"])
        icount = item.get("count", 0)
        href = item["href"]
        entries_xml.append(f"""  <entry>
    <title>{iname} ({icount:,})</title>
    <id>{feed_id}:{iid}</id>
    <updated>{updated}</updated>
    <content type="text">{icount:,} books available</content>
    <link rel="subsection" href="{href}" type="{OPDS_MIME_ACQUISITION}" />
  </entry>""")

    all_entries = "\n".join(entries_xml)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>{feed_id}</id>
  <title>{_escape(title)}</title>
  <updated>{updated}</updated>
  <link rel="self" href="{self_url}" type="{OPDS_MIME_NAVIGATION}" />
  <link rel="start" href="{base_url}/opds" type="{OPDS_MIME_NAVIGATION}" />
  <link rel="search" href="{base_url}/opds/opensearch.xml" type="{OPENSEARCH_MIME}" />
{all_entries}
</feed>"""
