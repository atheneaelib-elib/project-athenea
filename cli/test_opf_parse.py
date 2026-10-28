import re
import xml.etree.ElementTree as ET
from pathlib import Path

def parse_calibre_opf(opf_path: Path) -> dict:
    raw = opf_path.read_bytes()
    encoding = 'utf-8'
    enc_m = re.search(rb'<\?xml[^>]+encoding=[\'"]([^\'"]+)[\'"]', raw[:200])
    if enc_m:
        try:
            encoding = enc_m.group(1).decode('ascii', errors='ignore').strip()
        except Exception:
            pass

    text = raw.decode(encoding, errors='replace')
    clean_text = re.sub(r'(</?)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)', r'\1\2', text)
    clean_text = re.sub(r'(\s)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)=', r'\1\2=', clean_text)

    root = ET.fromstring(clean_text)

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
    }

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
        elif tag == "description" and elem.text and not meta["comments"]:
            meta["comments"] = elem.text.strip()
        elif tag == "subject" and elem.text:
            s = elem.text.strip()
            if s and s not in meta["tags"]:
                meta["tags"].append(s)
        elif tag == "date" and elem.text and not meta["pubdate"]:
            meta["pubdate"] = elem.text.strip()
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

    return meta

sample_opf = Path('/mnt/mainlib/Books/EBLib/Ian-Viernier Miullier/Chto takoie populizm_ (281356)/metadata.opf')
res = parse_calibre_opf(sample_opf)
for k, v in res.items():
    if k == "comments":
        print(f"{k}: {v[:60] if v else 'None'}...")
    else:
        print(f"{k}: {v}")
