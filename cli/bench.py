import os
import time
import re
import xml.etree.ElementTree as ET

files = []
for entry in os.scandir('/mnt/mainlib/Books/librusec'):
    if entry.name.endswith('.fb2'):
        files.append(entry.path)
        if len(files) >= 1000:
            break

t0 = time.time()
parsed = 0
for f in files:
    with open(f, 'rb') as fp:
        raw = fp.read(128 * 1024)
    m = re.search(rb'<description>.*?</description>', raw, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            root = ET.fromstring(b'<fb2>' + m.group(0) + b'</fb2>')
            parsed += 1
        except Exception:
            pass

elapsed = time.time() - t0
print(f"Parsed {parsed}/{len(files)} files in {elapsed:.3f}s ({len(files)/elapsed:.1f} files/sec single thread)")
