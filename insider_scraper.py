import requests
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

# Gebruik yfinance voor de actuele beurskoersen
try:
    import yfinance as yf
except ImportError:
    yf = None

# --- CONFIGURATIE ---
BASE_URL = "https://www.sec.gov"
RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&owner=include&count=100&output=atom"
# BELANGRIJK: Gebruik een realistisch User-Agent om blokkades te voorkomen
HEADERS = {"User-Agent": "InsiderScannerPro/2.0 (contact: reinier@stumpel.com)"}

OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)

# Bestandsnamen die exact overeenkomen met je index.html
PORTFOLIO_FILE = OUTPUT_DIR / "fictional_portfolios.json"
LIVE_FEED_FILE = OUTPUT_DIR / "live_feed.json"
REJECTED_FILE = OUTPUT_DIR / "rejected_filings.json"
HEARTBEAT_FILE = OUTPUT_DIR / "heartbeat.json"

VIP_ROLES = ["ceo", "cfo", "director", "president", "chief executive", "chief financial", "board member", "chairman"]

def get_current_price(ticker):
    if yf is None: return 0.0
    try:
        stock = yf.Ticker(ticker)
        # Gebruik fast_info of regular market price
        price = stock.fast_info['last_price']
        return round(price, 2)
    except:
        return 0.0

def get_detailed_info(filing_url):
    """Analyseert Form 4 XML voor transactiedetails."""
    data = {"role": "unknown", "value": 0.0, "is_vip": False, "price": 0.0, "type": "4"}
    try:
        time.sleep(0.15) # SEC rate limit respecteren
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
            
            if code == 'P': # Purchase
                total_value += float(shares) * float(price)
                last_price = float(price)
        
        data["value"] = total_value
        data["price"] = last_price
    except Exception as e:
        print(f"Detail error voor {filing_url}: {e}")
    return data

def evaluate_strategies(finding, current_portfolios):
    """Bepaalt of een aankoop in een strategie past."""
    triggered = []
    reasons = []

    # 1. VIP Follower
    if finding['is_vip'] and finding['value'] >= 50000:
        triggered.append("VIP_Follower")
    elif finding['type'] == '4':
        if not finding['is_vip']: reasons.append("Geen VIP rol")
        if finding['value'] < 50000: reasons.append(f"Waarde ${finding['value']:.0f} < $50k")

    # 2. Whale Watcher
    if finding['type'] in ['13D', '13G'] or finding['value'] >= 500000:
        triggered.append("Whale_Watcher")
    elif finding['value'] < 500000 and finding['type'] not in ['13D', '13G']:
        reasons.append("Waarde te laag voor Whale")

    # 3. Cluster Hunter (Simpele versie: koop als waarde > 100k)
    if finding['value'] >= 100000:
        triggered.append("Cluster_Hunter")
    else:
        reasons.append("Geen cluster/lage waarde")

    return list(set(triggered)), "; ".join(reasons)

def run_scraper():
    print(f"Start scan: {datetime.now()}")
    
    # Heartbeat bijwerken
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump({"last_run": datetime.now().isoformat()}, f)

    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
        entries = re.findall(r'<entry>.*?</entry>', resp.text, re.DOTALL)
    except Exception as e:
        print(f"RSS Error: {e}")
        return

    new_findings = []
    live_feed_data = []
    
    for entry in entries[:20]: # Check de laatste 20 meldingen
        try:
            ticker = re.search(r'<title>(.*?) \(', entry).group(1)
            link = re.search(r'<link [^>]*href="(.*?)"', entry).group(1)
            form_type = re.search(r'<term>(.*?)</term>', entry).group(1)
            
            if form_type in ['4', '13D', '13G']:
                details = get_detailed_info(link)
                if details['value'] > 0:
                    finding = {
                        "ticker": ticker,
                        "type": form_type,
                        "value": details['value'],
                        "role": details['role'],
                        "is_vip": details['is_vip'],
                        "buy_price": details['price'],
                        "timestamp": datetime.now().strftime("%H:%M:%S"),
                        "form_type": form_type
                    }
                    new_findings.append(finding)
                    live_feed_data.append(finding)
        except:
            continue

    # Portefeuilles updaten
    update_logic(new_findings)
    
    # Live feed opslaan
    with open(LIVE_FEED_FILE, "w") as f:
        json.dump(live_feed_data[:15], f)

def update_logic(new_findings):
    # Laden bestaande data
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, "r") as f: portfolios = json.load(f)
    else:
        portfolios = {
            "VIP_Follower": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Whale_Watcher": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Cluster_Hunter": {"balance": 10000.0, "positions": [], "total_profit": 0.0}
        }

    rejected_log = []

    # Update huidige posities (P/L)
    for strat in portfolios:
        total_pnl = 0.0
        for pos in portfolios[strat]['positions']:
            current_price = get_current_price(pos['ticker'])
            if current_price > 0:
                pos['current_price'] = current_price
                pos['pnl_percent'] = round(((current_price - pos['buy_price']) / pos['buy_price']) * 100, 2)
                pos['pnl_usd'] = round((1000.0 * (pos['pnl_percent'] / 100)), 2)
                total_pnl += pos['pnl_usd']
        portfolios[strat]['total_profit'] = round(total_pnl, 2)

    # Nieuwe trades verwerken
    for f in new_findings:
        strats, reject_reason = evaluate_strategies(f, portfolios)
        
        if strats:
            for s in strats:
                # Alleen kopen als we ticker nog niet hebben
                if not any(p['ticker'] == f['ticker'] for p in portfolios[s]['positions']):
                    if portfolios[s]['balance'] >= 1000:
                        portfolios[s]['balance'] -= 1000
                        portfolios[s]['positions'].append({
                            "ticker": f['ticker'],
                            "buy_price": f['buy_price'],
                            "amount": 1000,
                            "pnl_percent": 0.0,
                            "pnl_usd": 0.0
                        })
        else:
            rejected_log.append({
                "ticker": f['ticker'],
                "insider_role": f['role'],
                "value": f['value'],
                "rejection_reason": reject_reason if reject_reason else "Niet interessant",
                "timestamp": f['timestamp'],
                "form_type": f['type']
            })

    # Opslaan
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolios, f, indent=4)
    
    with open(REJECTED_FILE, "w") as f:
        json.dump(rejected_log[:20], f, indent=4)

if __name__ == "__main__":
    run_scraper()
