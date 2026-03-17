import requests
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import defaultdict
from pathlib import Path

# CONFIG
BASE_URL = "https://www.sec.gov"
RSS_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&owner=include&count=100&output=atom"
HEADERS = {"User-Agent": "InsiderScanner/1.0 (contact: research@example.com)"}
OUTPUT_DIR = Path("insider_data")
OUTPUT_DIR.mkdir(exist_ok=True)
PORTFOLIO_FILE = OUTPUT_DIR / "fictional_portfolios.json"

VIP_ROLES = ["ceo", "cfo", "director", "president", "chief executive", "chief financial", "board member", "chairman"]

# --- STRATEGIE LOGICA ---
def evaluate_strategies(finding, clusters):
    triggered_strategies = []
    
    # 1. VIP Follower: VIP aankoop > $50k
    if finding['is_vip'] and finding['value'] >= 50000:
        triggered_strategies.append("VIP_Follower")
        
    # 2. Whale Watcher: 13D/G of waarde > $500k
    if finding['type'] in ['13D', '13G'] or finding['value'] >= 500000:
        triggered_strategies.append("Whale_Watcher")
        
    # 3. Cluster Hunter: Deel van een Ultra Conviction cluster
    for c in clusters:
        if c['ticker'] == finding['ticker'] and "ULTRA" in c['status']:
            triggered_strategies.append("Cluster_Hunter")
            break
            
    return list(set(triggered_strategies))

def update_portfolios(new_findings, clusters):
    # Laad bestaande portfolios of start nieuw
    if PORTFOLIO_FILE.exists():
        with open(PORTFOLIO_FILE, "r") as f:
            portfolios = json.load(f)
    else:
        portfolios = {
            "VIP_Follower": {"balance": 100000, "positions": []},
            "Whale_Watcher": {"balance": 100000, "positions": []},
            "Cluster_Hunter": {"balance": 100000, "positions": []}
        }

    for f in new_findings:
        strats = evaluate_strategies(f, clusters)
        for s in strats:
            # Check of we dit aandeel al hebben (om dubbel kopen te voorkomen)
            existing = any(p['ticker'] == f['ticker'] for p in portfolios[s]['positions'])
            if not existing:
                # Fictieve aankoop: we 'investeren' $5000 per trade
                investment = 5000
                portfolios[s]['positions'].append({
                    "ticker": f['ticker'],
                    "buy_price": f.get('price_estimate', 100.0), # Dummy prijs bij gebrek aan live API
                    "buy_date": datetime.now().strftime("%Y-%m-%d"),
                    "reason": f"Type: {f['type']}, Role: {f['role']}, Value: ${f['value']}",
                    "amount": investment
                })
                portfolios[s]['balance'] -= investment

    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolios, f, indent=2)

# --- BESTAANDE FUNCTIES (Aangepast voor detail) ---

def get_detailed_info(filing_url):
    data = {"role": "unknown", "value": 0.0, "is_vip": False, "price_estimate": 0.0}
    try:
        time.sleep(0.1) 
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
        avg_price = 0.0
        for trans in root.findall(".//nonDerivativeTransaction"):
            shares = trans.findtext(".//transactionShares/value", "0")
            price = trans.findtext(".//transactionPricePerShare/value", "0")
            if trans.findtext(".//transactionCoding/transactionCode", "") == 'P':
                total_value += float(shares) * float(price)
                avg_price = float(price)
        
        data["value"] = total_value
        data["price_estimate"] = avg_price
            
    except: pass
    return data

def fetch_recent_filings():
    resp = requests.get(RSS_URL, headers=HEADERS, timeout=15)
    root = ET.fromstring(resp.content)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    findings = []
    for entry in root.findall("atom:entry", ns):
        title = entry.findtext("atom:title", "", ns)
        link = entry.find("atom:link", ns).attrib.get("href", "")
        m = re.match(r"^(4|13D|13G|8-K|13F)\s+-\s+(.+?)\s+\(([A-Z]{1,6})\)", title)
        if m:
            form_type, ticker = m.group(1), m.group(3)
            detail = get_detailed_info(link) if form_type == '4' else {"role": "unknown", "value": 0.0, "is_vip": False, "price_estimate": 0.0}
            findings.append({
                "ticker": ticker, "type": form_type, "role": detail["role"],
                "is_vip": detail["is_vip"], "value": detail["value"], 
                "price_estimate": detail["price_estimate"], "link": link
            })
    return findings

def detect_clusters(filings):
    ticker_map = defaultdict(list)
    for f in filings: ticker_map[f['ticker']].append(f)
    clusters = []
    for ticker, group in ticker_map.items():
        total_val = sum(f['value'] for f in group)
        vips = [f for f in group if f['is_vip']]
        if len(group) > 1:
            status = "Normal"
            if len(vips) >= 2 and total_val > 100000: status = "🚀 ULTRA CONVICTION"
            clusters.append({"ticker": ticker, "status": status})
    return clusters

def main():
    all_filings = fetch_recent_filings()
    clusters = detect_clusters(all_filings)
    
    # Update de fictieve portfolios op basis van de nieuwe data
    update_portfolios(all_filings, clusters)
    
    # Opslaan van algemene data
    output = {
        "updated": datetime.now().isoformat(),
        "total_filings": len(all_filings),
        "clusters": clusters
    }
    with open(OUTPUT_DIR / "latest.json", "w") as f:
        json.dump(output, f, indent=2)
    print("Scan voltooid en Portfolios bijgewerkt.")

if __name__ == "__main__":
    main()
