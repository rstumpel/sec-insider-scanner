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
HEADERS = {"User-Agent": "InsiderScannerPro/2.0 (contact: reinier@stumpel.com)"}
OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)

PORTFOLIO_FILE = OUTPUT_DIR / "fictional_portfolios.json"
LIVE_FEED_FILE = OUTPUT_DIR / "live_feed.json"
REJECTED_FILE = OUTPUT_DIR / "rejected_filings.json" # Nieuw bestand voor de tabel

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
            # Alleen aankopen (P) tellen, geen verkopen (S) of opties
            if trans.findtext(".//transactionCoding/transactionCode", "") == 'P':
                total_value += float(shares) * float(price)
                last_price = float(price)
        
        data["value"] = total_value
        data["price"] = last_price
    except: pass
    return data

def evaluate_strategies(finding, clusters):
    """Bepaalt of een filing wordt gekocht OF waarom deze wordt afgewezen."""
    triggered = []
    reasons = []

    # 1. Check VIP Follower
    if finding['is_vip'] and finding['value'] >= 50000:
        triggered.append("VIP_Follower")
    elif finding['type'] == '4':
        if not finding['is_vip']: reasons.append("Geen VIP rol")
        if finding['value'] < 50000: reasons.append(f"Waarde ${finding['value']:.0f} < $50k")

    # 2. Check Whale Watcher
    if finding['type'] in ['13D', '13G'] or finding['value'] >= 500000:
        triggered.append("Whale_Watcher")
    elif finding['value'] < 500000 and finding['type'] not in ['13D', '13G']:
        reasons.append("Waarde te laag voor Whale strategie")

    # 3. Check Cluster Hunter
    is_cluster = False
    for c in clusters:
        if c['ticker'] == finding['ticker'] and "ULTRA" in c['status']:
            triggered.append("Cluster_Hunter")
            is_cluster = True
    if not is_cluster and finding['type'] == '4':
        reasons.append("Geen koop-cluster gedetecteerd")

    # Algemene afwijzingen voor andere formulieren
    if finding['type'] in ['8-K', '13F']:
        reasons.append(f"Formulier {finding['type']} is alleen informatief")

    return list(set(triggered)), "; ".join(list(set(reasons)))

def update_portfolios(new_findings, clusters):
    """Beheert portefeuilles en houdt een logboek bij van afwijzingen."""
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r") as f:
                portfolios = json.load(f)
        except: portfolios = None
            
    if not portfolios:
        portfolios = {
            "VIP_Follower": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Whale_Watcher": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Cluster_Hunter": {"balance": 10000.0, "positions": [], "total_profit": 0.0}
        }

    rejected_log = []

    # Bestaande posities updaten
    for strat in portfolios:
        current_strat_profit = 0.0
        for pos in portfolios[strat]['positions']:
            live_price = get_current_price(pos['ticker'])
            if live_price > 0:
                pos['current_price'] = live_price
                pos['pnl_percent'] = round(((live_price - pos['buy_price']) / pos['buy_price']) * 100, 2)
                pos['pnl_usd'] = round((pos['amount'] * (pos['pnl_percent'] / 100)), 2)
                current_strat_profit += pos['pnl_usd']
        portfolios[strat]['total_profit'] = round(current_strat_profit, 2)

    # Nieuwe bevindingen verwerken
    for f in new_findings:
        strats, reject_reason = evaluate_strategies(f, clusters)
        
        if strats:
            for s in strats:
                if not any(p['ticker'] == f['ticker'] for p in portfolios[s]['positions']):
                    investment = 1000.0
                    buy_price = f['price'] if f['price'] > 0 else get_current_price(f['ticker'])
                    
                    if portfolios[s]['balance'] >= investment and buy_price > 0:
                        portfolios[s]['positions'].append({
                            "ticker": f['ticker'],
                            "buy_price": buy_price,
                            "current_price": buy_price,
                            "buy_date": datetime.now().strftime("%Y-%m-%d"),
                            "amount": investment,
                            "pnl_percent": 0.0,
                            "pnl_usd": 0.0,
                            "reason": f"Type: {f['type']}, Role: {f['role']}"
                        })
                        portfolios[s]['balance'] -= investment
        else:
            # Als het door geen enkele strategie is gekocht, voeg toe aan afwijzingen
            rejected_log.append({
                "ticker": f.get("ticker", "???"),
                "form_type": f.get("type", "UNK"),
                "insider_role": f.get("role", "N/B"),
                "value": f.get("value", 0),
                "rejection_reason": reject_reason if reject_reason else "Voldoet niet aan criteria",
                "timestamp": f.get("time", datetime.now().strftime("%H:%M"))
            })

    # Opslaan Portefeuilles
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolios, f, indent=2)

    # Opslaan Afwijzingen (beperk tot laatste 50 voor snelheid)
    with open(REJECTED_FILE, "w") as f:
        json.dump(rejected_log[:50], f, indent=2)

def fetch_recent_filings():
    resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    root = ET.fromstring(resp.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    findings = []
    
    for entry in root.findall("atom:entry", ns):
        title = entry.findtext("atom:title", "", ns)
        link = entry.find("atom:link", ns).attrib.get("href", "")
        # Regex aangepast om meer types te vangen
        m = re.match(r"^(4|13D|13G|8-K|13F|D)\s+-\s+(.+?)\s+\(([A-Z0-9]{1,6})\)", title)
        
        if m:
            form_type, ticker = m.group(1), m.group(3)
            detail = get_detailed_info(link) if form_type == '4' else {"role": "N/B", "value": 0.0, "is_vip": False, "price": 0.0}
            findings.append({
                "ticker": ticker, "type": form_type, "role": detail["role"],
                "is_vip": detail["is_vip"], "value": detail["value"], 
                "price": detail["price"], "link": link,
                "time": datetime.now().strftime("%H:%M")
            })
    return findings

def detect_clusters(filings):
    ticker_map = defaultdict(list)
    for f in filings: ticker_map[f['ticker']].append(f)
    clusters = []
    for ticker, group in ticker_map.items():
        vips = [f for f in group if f['is_vip'] and f['value'] > 0]
        status = "Normal"
        if len(vips) >= 2: status = "🚀 ULTRA CONVICTION"
        clusters.append({"ticker": ticker, "status": status})
    return clusters

def main():
    print(f"🚀 Scanner gestart...")
    all_filings = fetch_recent_filings()
    
    # Live Feed (Radar)
    with open(LIVE_FEED_FILE, "w") as f:
        json.dump(all_filings[:25], f, indent=2)

    clusters = detect_clusters(all_filings)
    update_portfolios(all_filings, clusters)
    
    print(f"✅ Klaar. {len(all_filings)} filings verwerkt.")

if __name__ == "__main__":
    main()
