import requests
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# Gebruik yfinance voor de actuele beurskoersen
try:
    import yfinance as yf
except ImportError:
    yf = None

# --- CONFIGURATIE ---
BASE_URL = "https://www.sec.gov"
RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&owner=include&count=100&output=atom"
# PAS DIT AAN: Gebruik je eigen e-mailadres voor de SEC
HEADERS = {"User-Agent": "InsiderScannerPro/2.0 (contact: jouw-email@gmail.com)"}
OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)

PORTFOLIO_FILE = OUTPUT_DIR / "fictional_portfolios.json"
LIVE_FEED_FILE = OUTPUT_DIR / "live_feed.json"
REJECTED_FILE = OUTPUT_DIR / "rejected_filings.json"
HEARTBEAT_FILE = OUTPUT_DIR / "heartbeat.json"

VIP_ROLES = ["ceo", "cfo", "director", "president", "chief executive", "chief financial", "board member", "chairman"]

def get_current_price(ticker):
    if yf is None: return 0.0
    try:
        stock = yf.Ticker(ticker)
        price = stock.fast_info['last_price']
        return round(price, 2)
    except:
        return 0.0

def get_detailed_info(filing_url):
    data = {"role": "unknown", "value": 0.0, "is_vip": False, "price": 0.0}
    try:
        time.sleep(0.15) 
        resp = requests.get(filing_url, headers=HEADERS, timeout=10)
        xml_match = re.search(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
        if not xml_match: return data
        
        xml_url = BASE_URL + xml_match.group(1)
        xml_resp = requests.get(xml_url, headers=HEADERS, timeout=10)
        root = ET.fromstring(xml_resp.content)
        
        title_elem = root.find(".//officerTitle")
        role_text = title_elem.text.lower() if title_elem is not None and title_elem.text else ""
        is_dir = root.find(".//isDirector")
        
        if any(vip in role_text for vip in VIP_ROLES) or (is_dir is not None and is_dir.text in ["1", "true"]):
            data["is_vip"] = True
            data["role"] = role_text if role_text else "director"

        total_value = 0.0
        last_price = 0.0
        for trans in root.findall(".//nonDerivativeTransaction"):
            shares = trans.findtext(".//transactionShares/value", "0")
            price = trans.findtext(".//transactionPricePerShare/value", "0")
            if trans.findtext(".//transactionCoding/transactionCode", "") == 'P':
