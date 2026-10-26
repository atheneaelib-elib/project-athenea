import re
import os
import base64
import xml.etree.ElementTree as ET

def parse_fb2_complete(fb2_path):
    with open(fb2_path, 'rb') as f:
        # Read first 512KB for description
        header_bytes = f.read(512 * 1024)

    encoding = 'utf-8'
    enc_m = re.search(rb'<\?xml[^>]+encoding=[\'"]([^\'"]+)[\'"]', header_bytes[:300])
    if enc_m:
        try:
            encoding = enc_m.group(1).decode('ascii', errors='ignore').strip()
        except Exception:
            pass

    header_text = header_bytes.decode(encoding, errors='replace')

    meta = {
        "title": None,
        "author": None,
        "authors": [],
        "tags": [],
        "series": None,
        "series_index": 1.0,
        "comments": None,
        "publisher": None,
        "pubdate": None,
        "isbn": None,
        "cover_id": None,
        "cover_bytes": None,
    }

    desc_m = re.search(r'<description[^>]*>(.*?)</description>', header_text, re.DOTALL | re.IGNORECASE)
    if desc_m:
        desc_content = desc_m.group(1)
        # Strip all namespace prefixes e.g. <l:a>, <image l:href...>, <p:first-name>
        clean_desc = re.sub(r'(</?)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)', r'\1\2', desc_content)
        clean_desc = re.sub(r'(\s)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)=', r'\1\2=', clean_desc)

        try:
            root = ET.fromstring(f"<description>{clean_desc}</description>")
            for elem in root.iter():
                tag = elem.tag.lower()

                if tag == "book-title" and elem.text and not meta["title"]:
                    meta["title"] = elem.text.strip()

                elif tag == "author":
                    first, middle, last, nick = "", "", "", ""
                    for child in elem:
                        ctag = child.tag.lower()
                        if ctag == "first-name" and child.text:
                            first = child.text.strip()
                        elif ctag == "middle-name" and child.text:
                            middle = child.text.strip()
                        elif ctag == "last-name" and child.text:
                            last = child.text.strip()
                        elif ctag == "nickname" and child.text:
                            nick = child.text.strip()
                    full = " ".join([p for p in (first, middle, last) if p]) or nick
                    if full and full not in meta["authors"]:
                        meta["authors"].append(full)

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
                    texts = [p.text.strip() for p in elem.iter() if p.text and p.text.strip()]
                    if texts:
                        meta["comments"] = "\n\n".join(texts)

                elif tag == "publisher" and not meta["publisher"]:
                    texts = [p.text.strip() for p in elem.iter() if p.tag.lower() != "id" and p.text and p.text.strip()]
                    if texts:
                        meta["publisher"] = " ".join(texts)

                elif tag == "isbn" and elem.text:
                    meta["isbn"] = elem.text.strip().replace("-", "")

                elif tag == "date" and elem.text and not meta["pubdate"]:
                    meta["pubdate"] = elem.text.strip()

                elif tag == "image" and not meta["cover_id"]:
                    for k, v in elem.attrib.items():
                        if k.lower().endswith("href"):
                            meta["cover_id"] = v.lstrip("#")
        except Exception as e:
            print("XML Error:", e)

    # Fallback to regex if needed
    if not meta["title"]:
        m_t = re.search(r'<book-title[^>]*>(.*?)</book-title>', header_text, re.DOTALL | re.IGNORECASE)
        if m_t:
            meta["title"] = re.sub(r'<[^>]+>', '', m_t.group(1)).strip()

    if not meta["publisher"]:
        m_pub = re.search(r'<publisher[^>]*>(.*?)</publisher>', header_text, re.DOTALL | re.IGNORECASE)
        if m_pub:
            meta["publisher"] = re.sub(r'<[^>]+>', '', m_pub.group(1)).strip()

    if not meta["comments"]:
        m_annot = re.search(r'<annotation[^>]*>(.*?)</annotation>', header_text, re.DOTALL | re.IGNORECASE)
        if m_annot:
            meta["comments"] = re.sub(r'<[^>]+>', ' ', m_annot.group(1)).strip()

    if not meta["cover_id"]:
        m_cov = re.search(r'<coverpage[^>]*>.*?<image[^>]+href=[\'"]#?([^\'"]+)[\'"]', header_text, re.DOTALL | re.IGNORECASE)
        if m_cov:
            meta["cover_id"] = m_cov.group(1).lstrip("#")

    # Read binary for cover
    with open(fb2_path, "rb") as f:
        full_bytes = f.read()

    # Try specific cover_id first
    if meta["cover_id"]:
        cid_bytes = meta["cover_id"].encode(encoding, errors="ignore")
        pat = rb'<binary[^>]+id=[\'"]' + re.escape(cid_bytes) + rb'[\'"][^>]*>(.*?)</binary>'
        m_b = re.search(pat, full_bytes, re.DOTALL | re.IGNORECASE)
        if m_b:
            try:
                meta["cover_bytes"] = base64.b64decode(m_b.group(1).strip())
            except Exception:
                pass

    # If still no cover, search for any binary with 'cover' in ID
    if not meta["cover_bytes"]:
        m_b = re.search(rb'<binary[^>]+id=[\'"][^\'"]*cover[^\'"]*[\'"][^>]*>(.*?)</binary>', full_bytes, re.DOTALL | re.IGNORECASE)
        if m_b:
            try:
                meta["cover_bytes"] = base64.b64decode(m_b.group(1).strip())
            except Exception:
                pass

    meta["author"] = meta["authors"][0] if meta["authors"] else "Unknown Author"
    return meta

files = [
    '/mnt/mainlib/Books/librusec/822086.fb2',
    '/mnt/mainlib/Books/librusec/736122.fb2',
    '/mnt/mainlib/Books/librusec/472933.fb2',
    '/mnt/mainlib/Books/librusec/745180.fb2',
    '/mnt/mainlib/Books/librusec/401991.fb2',
    '/mnt/mainlib/Books/librusec/787051.fb2',
    '/mnt/mainlib/Books/librusec/472541.fb2',
    '/mnt/mainlib/Books/librusec/111122.fb2',
]

for f in files:
    m = parse_fb2_complete(f)
    print(f"\n{os.path.basename(f)}:")
    print(f"  Title: {m['title']}")
    print(f"  Author: {m['author']}")
    print(f"  Publisher: {m['publisher']}")
    print(f"  Has Cover: {m['cover_bytes'] is not None} (len: {len(m['cover_bytes']) if m['cover_bytes'] else 0})")
    print(f"  Has Description: {m['comments'] is not None} (len: {len(m['comments']) if m['comments'] else 0})")
