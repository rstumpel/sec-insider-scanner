import requests
import json
import time
import argparse
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# CONFIG
BASE_URL = "https://www.sec.gov"
RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=include&count=40&output=atom"
HEADERS = {"User-Agent": "InsiderScanner/1.0 (contact: research@example.com)"}
OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)

def fetch_rss_entries(max_entries=40):
    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = []
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", "", ns)
            link_elem = entry.find("atom:link", ns)
            link = link_elem.attrib.get("href", "") if link_elem is not None else ""
            m = re.match(r"^4\s+-\s+(.+?)\s+\(([A-Z]{1,6})\)", title)
            if m:
                entries.append({"company": m.group(1).strip(), "ticker": m.group(2), "link": link})
        return entries[:max_entries]
    except Exception as e:
        print(f"Fout bij ophalen RSS: {e}")
        return []

def main():
    print(f"[{datetime.now()}] Start scan...")
    entries = fetch_rss_entries()
    out_path = OUTPUT_DIR / "latest.json"
    payload = {"scraped_at": datetime.now().isoformat(), "results": entries}
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Klaar! {len(entries)} entries opgeslagen in {out_path}")

if __name__ == "__main__":
    main()
