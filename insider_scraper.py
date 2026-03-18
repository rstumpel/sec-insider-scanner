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
# Belangrijk: De SEC vereist een duidelijke User-Agent om data te mogen scrapen
HEADERS = {"User-Agent": "InsiderScannerPro/2.0 (contact: research@example.com)"}
OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)
PORTFOLIO_FILE = OUTPUT_DIR / "fictional_portfolios.json"
LIVE_FEED_FILE = OUTPUT_DIR / "live_feed.json"

# Rollen die we als 'belangrijk' beschouwen
VIP_ROLES = ["ceo", "cfo", "director", "president", "chief executive", "chief financial", "board member", "chairman"]

def get_current_price(ticker):
    """Haalt de meest recente koers op via Yahoo Finance."""
    if yf is None: return 0.0
    try:
        stock = yf.Ticker(ticker)
        # We pakken de 'last_price' voor een snelle update
        price = stock.fast_info['last_price']
        return round(price, 2)
    except:
        return 0.0

def get_detailed_info(filing_url):
    """Analyseert een specifiek Form 4 document voor transactiedetails."""
    data = {"role": "unknown", "value": 0.0, "is_vip": False, "price": 0.0}
    try:
        time.sleep(0.15) # SEC limiet respecteren (10 requests per seconde)
        resp = requests.get(filing_url, headers=HEADERS, timeout=10)
        xml_match = re.search(r'href="(/Archives/edgar/data/[^"]+\.xml)"', resp.text)
        if not xml_match: return data
        
        xml_url = BASE_URL + xml_match.group(1)
        xml_resp = requests.get(xml_url, headers=HEADERS, timeout=10)
        root = ET.fromstring(xml_resp.content)
        
        title_elem = root.find(".//officerTitle")
        role_text = title_elem.text.lower() if title_elem is not None and title_elem.text else ""
        is_dir = root.find(".//isDirector")
        
        # Is de insider een kopstuk?
        if any(vip in role_text for vip in VIP_ROLES) or (is_dir is not None and is_dir.text in ["1", "true"]):
            data["is_vip"] = True
            data["role"] = role_text if role_text else "director"

        total_value = 0.0
        last_price = 0.0
        # Alleen kijken naar 'P' (Purchase/Aankoop)
        for trans in root.findall(".//nonDerivativeTransaction"):
            shares = trans.findtext(".//transactionShares/value", "0")
            price = trans.findtext(".//transactionPricePerShare/value", "0")
            if trans.findtext(".//transactionCoding/transactionCode", "") == 'P':
                total_value += float(shares) * float(price)
                last_price = float(price)
        
        data["value"] = total_value
        data["price"] = last_price
    except: pass
    return data

def evaluate_strategies(finding, clusters):
    """Checkt of een melding voldoet aan de eisen van de 3 strategieën."""
    triggered = []
    # 1. VIP Follower: Belangrijk persoon koopt voor meer dan $50.000
    if finding['is_vip'] and finding['value'] >= 50000: 
        triggered.append("VIP_Follower")
    
    # 2. Whale Watcher: Extreem grote bedragen (> $500k) of institutionele 13D/G meldingen
    if finding['type'] in ['13D', '13G'] or finding['value'] >= 500000: 
        triggered.append("Whale_Watcher")
    
    # 3. Cluster Hunter: Als er een 'ULTRA' status is door meerdere kopers
    for c in clusters:
        if c['ticker'] == finding['ticker'] and "ULTRA" in c['status']:
            triggered.append("Cluster_Hunter")
            
    return list(set(triggered))

def update_portfolios(new_findings, clusters):
    """Beheert de virtuele portefeuilles, startkapitaal en P/L berekening."""
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r") as f:
                portfolios = json.load(f)
        except:
            portfolios = None
            
    if not portfolios:
        # HIER IS JE NIEUWE STARTKAPITAAL: 10.000 per strategie
        portfolios = {
            "VIP_Follower": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Whale_Watcher": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Cluster_Hunter": {"balance": 10000.0, "positions": [], "total_profit": 0.0}
        }

    # Posities updaten met actuele marktwaarde
    for strat in portfolios:
        current_strat_profit = 0.0
        for pos in portfolios[strat]['positions']:
            live_price = get_current_price(pos['ticker'])
            if live_price > 0:
                pos['current_price'] = live_price
                # Profit/Loss berekening
                pos['pnl_percent'] = round(((live_price - pos['buy_price']) / pos['buy_price']) * 100, 2)
                pos['pnl_usd'] = round((pos['amount'] * (pos['pnl_percent'] / 100)), 2)
                current_strat_profit += pos['pnl_usd']
        portfolios[strat]['total_profit'] = round(current_strat_profit, 2)

    # Nieuwe kansen verzilveren
    for f in new_findings:
        strats = evaluate_strategies(f, clusters)
        for s in strats:
            # Voorkom dubbele aankopen van hetzelfde aandeel in één strategie
            if not any(p['ticker'] == f['ticker'] for p in portfolios[s]['positions']):
                investment = 1000.0 # We leggen standaard 10% van het startkapitaal in
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

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolios, f, indent=2)

def fetch_recent_filings():
    """Haalt de 100 meest recente SEC meldingen op."""
    resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    root = ET.fromstring(resp.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    findings = []
    
    for entry in root.findall("atom:entry", ns):
        title = entry.findtext("atom:title", "", ns)
        link = entry.find("atom:link", ns).attrib.get("href", "")
        # Filter op relevante formulier types
        m = re.match(r"^(4|13D|13G|8-K|13F)\s+-\s+(.+?)\s+\(([A-Z]{1,6})\)", title)
        
        if m:
            form_type, ticker = m.group(1), m.group(3)
            detail = get_detailed_info(link) if form_type == '4' else {"role": "unknown", "value": 0.0, "is_vip": False, "price": 0.0}
            findings.append({
                "ticker": ticker, "type": form_type, "role": detail["role"],
                "is_vip": detail["is_vip"], "value": detail["value"], 
                "price": detail["price"], "link": link,
                "time": datetime.now().strftime("%H:%M")
            })
    return findings

def detect_clusters(filings):
    """Detecteert of meerdere insiders tegelijk kopen bij één bedrijf."""
    ticker_map = defaultdict(list)
    for f in filings: ticker_map[f['ticker']].append(f)
    clusters = []
    for ticker, group in ticker_map.items():
        vips = [f for f in group if f['is_vip']]
        status = "Normal"
        if len(vips) >= 2: status = "🚀 ULTRA CONVICTION"
        clusters.append({"ticker": ticker, "status": status})
    return clusters

# --- UITVOERING ---
def main():
    print(f"🚀 Scanner gestart op {datetime.now().strftime('%H:%M:%S')}")
    
    # 1. Scrapen
    all_filings = fetch_recent_filings()
    print(f"🔎 {len(all_filings)} meldingen gescand.")
    
    # 2. Live Feed bijwerken (voor de radar op je dashboard)
    live_feed_data = all_filings[:25] # We tonen de laatste 25 meldingen
    with open(LIVE_FEED_FILE, "w") as f:
        json.dump(live_feed_data, f, indent=2)

    # 3. Portefeuilles updaten
    clusters = detect_clusters(all_filings)
    update_portfolios(all_filings, clusters)
    
    # 4. Status loggen
    with open(OUTPUT_DIR / "latest.json", "w") as f:
        json.dump({"updated": datetime.now().isoformat(), "count": len(all_filings)}, f, indent=2)
    
    print("✅ Portefeuilles en Live Radar zijn bijgewerkt.")

if __name__ == "__main__":
    main()
