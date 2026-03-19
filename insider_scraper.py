import requests
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
import yfinance as yf

# --- CONFIGURATIE ---
BASE_URL = "https://www.sec.gov"
RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&owner=include&count=40&output=atom"
HEADERS = {"User-Agent": "InsiderScannerPro/2.0 (contact: reinier@stumpel.com)"}

OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)

PORTFOLIO_FILE = OUTPUT_DIR / "fictional_portfolios.json"
LIVE_FEED_FILE = OUTPUT_DIR / "live_feed.json"
REJECTED_FILE = OUTPUT_DIR / "rejected_filings.json"
HEARTBEAT_FILE = OUTPUT_DIR / "heartbeat.json"

VIP_ROLES = ["ceo", "cfo", "director", "president", "chief executive", "chief financial", "board member", "chairman"]

def get_current_price(ticker):
    try:
        stock = yf.Ticker(ticker)
        return round(stock.fast_info['last_price'], 2)
    except:
        return 0.0

def get_detailed_info(filing_url):
    """Analyseert Form 4 XML voor transactiedetails."""
    data = {"role": "unknown", "value": 0.0, "is_vip": False, "price": 0.0, "type": "4"}
    try:
        time.sleep(0.2) # SEC rate limit
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
            code = trans.findtext(".//transactionCoding/transactionCode", "")
            if code == 'P': 
                total_value += float(shares) * float(price)
                last_price = float(price)
        
        data["value"] = total_value
        data["price"] = last_price
    except: pass
    return data

def run_scraper():
    print("Start run...")
    # 1. Heartbeat voor dashboard status
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump({"last_run": datetime.now().isoformat()}, f)

    # 2. Portfolio laden of maken
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, "r") as f: portfolios = json.load(f)
    else:
        portfolios = {
            "VIP_Follower": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Whale_Watcher": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Cluster_Hunter": {"balance": 10000.0, "positions": [], "total_profit": 0.0}
        }

    # 3. RSS Scrapen
    resp = requests.get(RSS_URL, headers=HEADERS)
    entries = re.findall(r'<entry>.*?</entry>', resp.text, re.DOTALL)
    
    new_trades = []
    rejected = []

    for entry in entries[:15]:
        try:
            ticker = re.search(r'<title>(.*?) \(', entry).group(1)
            link = re.search(r'<link [^>]*href="(.*?)"', entry).group(1)
            form = re.search(r'<term>(.*?)</term>', entry).group(1)
            
            details = get_detailed_info(link)
            if details['value'] > 0:
                finding = {"ticker": ticker, "value": details['value'], "role": details['role'], "is_vip": details['is_vip'], "buy_price": details['price'], "type": form, "timestamp": datetime.now().strftime("%H:%M:%S")}
                new_trades.append(finding)
                
                # Strategie logica
                # VIP: CEO/CFO > 50k
                if finding['is_vip'] and finding['value'] >= 50000:
                    if not any(p['ticker'] == ticker for p in portfolios['VIP_Follower']['positions']):
                        portfolios['VIP_Follower']['positions'].append({"ticker": ticker, "buy_price": finding['buy_price'], "amount": 1000, "pnl_percent": 0, "pnl_usd": 0})
                
                # Whale: Elke trade > 500k
                if finding['value'] >= 500000:
                    if not any(p['ticker'] == ticker for p in portfolios['Whale_Watcher']['positions']):
                        portfolios['Whale_Watcher']['positions'].append({"ticker": ticker, "buy_price": finding['buy_price'], "amount": 1000, "pnl_percent": 0, "pnl_usd": 0})
            else:
                rejected.append({"ticker": ticker, "insider_role": "n/a", "value": 0, "rejection_reason": "Geen directe aankoop", "timestamp": datetime.now().strftime("%H:%M:%S"), "form_type": form})
        except: continue

    # 4. P/L Updaten met yfinance
    for strat in portfolios:
        total_pnl = 0
        for pos in portfolios[strat]['positions']:
            cur = get_current_price(pos['ticker'])
            if cur > 0:
                pos['pnl_percent'] = round(((cur - pos['buy_price']) / pos['buy_price']) * 100, 2)
                pos['pnl_usd'] = round((pos['amount'] * (pos['pnl_percent'] / 100)), 2)
                total_pnl += pos['pnl_usd']
        portfolios[strat]['total_profit'] = round(total_pnl, 2)

    # 5. Opslaan
    with open(PORTFOLIO_FILE, "w") as f: json.dump(portfolios, f, indent=4)
    with open(LIVE_FEED_FILE, "w") as f: json.dump(new_trades[:10], f, indent=4)
    with open(REJECTED_FILE, "w") as f: json.dump(rejected[:10], f, indent=4)
    print("Klaar!")

if __name__ == "__main__":
    run_scraper()
