import re
import os
from pathlib import Path

files = []
for entry in os.scandir('/mnt/mainlib/Books/librusec'):
    if entry.name.endswith('.fb2'):
        files.append(entry.name)
        if len(files) >= 10:
            break

for fname in files:
    path = f'/mnt/mainlib/Books/librusec/{fname}'
    with open(path, 'rb') as f:
        full_content = f.read()

    print(f"\n=== FILE: {fname} (Size: {len(full_content)} bytes) ===")
    
    # 1. Encoding
    enc_m = re.search(rb'<\?xml[^>]+encoding=[\'"]([^\'"]+)[\'"]', full_content[:300])
    encoding = enc_m.group(1).decode('ascii', errors='ignore') if enc_m else 'utf-8'
    print(f"  Encoding: {encoding}")

    # 2. Coverpage
    cov = re.findall(rb'<coverpage>.*?</coverpage>', full_content[:200000], re.DOTALL | re.IGNORECASE)
    print(f"  Coverpage tag: {cov[:1]}")

    # 3. Binaries
    bins = re.findall(rb'<binary[^>]*id=[\'"]([^\'"]+)[\'"]', full_content, re.IGNORECASE)
    print(f"  Binary IDs total {len(bins)}: {bins[:5]}")

    # 4. Annotation
    annot = re.findall(rb'<annotation>.*?</annotation>', full_content[:200000], re.DOTALL | re.IGNORECASE)
    if annot:
        sample_annot = re.sub(rb'<[^>]+>', b'', annot[0]).strip()
        print(f"  Annotation text (sample): {sample_annot[:100].decode(encoding, errors='replace')}")
    else:
        print("  Annotation: None")

    # 5. Publisher
    pub = re.findall(rb'<publisher[^>]*>(.*?)</publisher>', full_content[:200000], re.DOTALL | re.IGNORECASE)
    if pub:
        print(f"  Publisher: {pub[0].decode(encoding, errors='replace')}")
    else:
        print("  Publisher: None")
