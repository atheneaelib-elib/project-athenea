import re
import base64
import xml.etree.ElementTree as ET

def test_parse(fb2_path):
    with open(fb2_path, 'rb') as f:
        header_bytes = f.read(512 * 1024)

    encoding = 'utf-8'
    enc_match = re.search(rb'<\?xml[^>]+encoding=[\'"]([^\'"]+)[\'"]', header_bytes[:300])
    if enc_match:
        try:
            encoding = enc_match.group(1).decode('ascii', errors='ignore').strip()
        except Exception:
            pass

    header_text = header_bytes.decode(encoding, errors='replace')

    desc_match = re.search(r'<description[^>]*>(.*?)</description>', header_text, re.DOTALL | re.IGNORECASE)
    if not desc_match:
        print("No description found")
        return

    desc_content = desc_match.group(1)
    # Strip namespace prefixes
    clean_desc = re.sub(r'(</?)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)', r'\1\2', desc_content)
    clean_desc = re.sub(r'(\s)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)=', r'\1\2=', clean_desc)

    root = ET.fromstring(f"<description>{clean_desc}</description>")

    meta = {
        "title": None,
        "authors": [],
        "cover_id": None,
        "annotation": None,
        "publisher": None,
    }

    for elem in root.iter():
        tag = elem.tag.lower()
        if tag == "book-title" and elem.text and not meta["title"]:
            meta["title"] = elem.text.strip()
        elif tag == "author":
            parts = [c.text.strip() for c in elem if c.text and c.text.strip()]
            if parts:
                meta["authors"].append(" ".join(parts))
        elif tag == "image" and not meta["cover_id"]:
            for k, v in elem.attrib.items():
                if k.lower().endswith("href"):
                    meta["cover_id"] = v.lstrip("#")
        elif tag == "annotation" and not meta["annotation"]:
            texts = [p.text.strip() for p in elem.iter() if p.text and p.text.strip()]
            meta["annotation"] = "\n\n".join(texts)
        elif tag == "publisher" and not meta["publisher"]:
            texts = [p.text.strip() for p in elem.iter() if p.text and p.text.strip()]
            meta["publisher"] = " ".join(texts)

    # Now test cover extraction
    cover_size = 0
    if meta["cover_id"]:
        cid_bytes = meta["cover_id"].encode("utf-8", errors="ignore")
        with open(fb2_path, "rb") as f:
            content = f.read()
        pat = rb'<binary[^>]*id=[\'"]' + re.escape(cid_bytes) + rb'[\'"][^>]*>(.*?)</binary>'
        m = re.search(pat, content, re.DOTALL | re.IGNORECASE)
        if m:
            cov_bytes = base64.b64decode(m.group(1).strip())
            cover_size = len(cov_bytes)

    print(f"File: {fb2_path}")
    print(f"  Title: {meta['title']}")
    print(f"  Authors: {meta['authors']}")
    print(f"  Cover ID: {meta['cover_id']} (Extracted Size: {cover_size} bytes)")
    print(f"  Publisher: {meta['publisher']}")
    print(f"  Annotation sample: {meta['annotation'][:80] if meta['annotation'] else 'None'}")

test_parse('/mnt/mainlib/Books/librusec/822086.fb2')
test_parse('/mnt/mainlib/Books/librusec/736122.fb2')
test_parse('/mnt/mainlib/Books/librusec/472933.fb2')
test_parse('/mnt/mainlib/Books/librusec/111122.fb2')
