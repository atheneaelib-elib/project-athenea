import xml.etree.ElementTree as ET
try:
    ET.fromstring('<description><image l:href="#cover.jpg"/></description>')
    print("SUCCESS")
except Exception as e:
    print(f"FAILED: {e}")
