import re

with open('/mnt/mainlib/Books/librusec/822086.fb2', 'rb') as f:
    raw = f.read()

bins = re.findall(rb'<binary[^>]*id=[\'"]([^\'"]+)[\'"]', raw, re.IGNORECASE)
print("All binary IDs in 822086.fb2:", bins)

cov = re.findall(rb'<coverpage>.*?</coverpage>', raw, re.DOTALL | re.IGNORECASE)
print("Coverpage:", cov)
