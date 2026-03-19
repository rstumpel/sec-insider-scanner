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
# BELANGRIJK: Vul hier je eigen e-mailadres in om blokkades te voorkomen
HEADERS = {"User-Agent": "InsiderScannerPro/2.0 (contact: reinier@stumpel.com)"}

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
    """Analyseert Form 4 XML voor transactiedetails."""
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
            
            # Check of het een aankoop (P) is
            if trans.findtext(".//transactionCoding/transactionCode", "") == 'P':
                # Deze regels moeten ingesprongen staan!
                total_value += float(shares) * float(price)
                last_price = float(price)
        
        data["value"] = total_value
        data["price"] = last_price
    except Exception as e:
        print(f"Detail error: {e}")
    return data

def evaluate_strategies(finding, clusters):
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

    # 3. Cluster Hunter
    is_cluster = False
    for c in clusters:
        if c['ticker'] == finding['ticker'] and "ULTRA" in c['status']:
            triggered.append("Cluster_Hunter")
            is_cluster = True
    if not is_cluster and finding['type'] == '4':
        reasons.append("Geen koop-cluster")

    return list(set(triggered)), "; ".join(reasons)

def update_portfolios(new_findings, clusters):
    """Beheert de virtuele portefeuilles en logs."""
    portfolios = None
    if PORTFOLIO_FILE.exists():
        try:
            with open(PORTFOLIO_FILE, "r") as f:
                portfolios = json.load(f)
        except:
            portfolios = None
            
    if not portfolios:
        portfolios = {
            "VIP_Follower": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Whale_Watcher": {"balance": 10000.0, "positions": [], "total_profit": 0.0},
            "Cluster_Hunter": {"balance": 10000.0, "positions": [], "total_profit": 0.0}
        }

    rejected_log = []

    # Marktupdate: Bereken huidige P/L
    for strat in portfolios:
        current_p = 0.0
        for pos in portfolios[strat]['positions']:
            live_price = get_current_price(pos['ticker'])
            if live_price > 0:
                pos['current_price'] = live_price
                pos['pnl_percent'] = round(((live_price - pos['buy_price']) / pos['buy_price']) * 100, 2)
                pos['pnl_usd'] = round((pos['amount'] * (pos['pnl_percent'] / 100)), 2)
                current_p += pos['pnl_usd']
        portfolios[strat]['total_profit'] = round(current_p, 2)

    # Nieuwe meldingen checken
    for f in new_findings:
        strats, reject_reason = evaluate_strategies(f, clusters)
        
        if strats:
            for s in strats:
                # Alleen kopen als we de ticker nog niet hebben in deze strategie
                if not any(p['ticker'] == f['ticker'] for p in portfolios[s]['positions']):
                    investment = 1000.0
