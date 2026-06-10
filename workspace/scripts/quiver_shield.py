import json
import subprocess
import os
import urllib.request
import time
from datetime import datetime, timedelta

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"

def get_quiver_token():
    return os.environ.get("QUIVER_API_KEY")

def run_subprocess(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()

def get_dpi(ticker):
    dpi_cmd = f"/root/.openclaw/workspace/quant_env/bin/python3 /root/.openclaw/workspace/skills/quiver-alpha/scripts/fetch.py darkpool {ticker}"
    dpi_out = run_subprocess(dpi_cmd)
    if dpi_out:
        try:
            dpi_data = json.loads(dpi_out)
            if dpi_data and len(dpi_data) > 0:
                # The data is a list of dicts, so dpi_data[0] is the latest entry
                return dpi_data[0].get("DPI", 0.5)
        except: pass
    return 0.5

def run_gog(cmd):
    full_cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip())
    except:
        return None

def get_congress_trades(ticker, token):
    url = f"https://api.quiverquant.com/beta/historical/congresstrading/{ticker}"
    headers = {
        "Authorization": f"Bearer {token}", 
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        res = urllib.request.urlopen(req).read()
        return json.loads(res)
    except Exception as e:
        print(f"Error fetching Quiver data for {ticker}: {e}")
        return []

def get_quiver_beta(endpoint, ticker, token):
    url = f"https://api.quiverquant.com/beta/historical/{endpoint}/{ticker}"
    headers = {
        "Authorization": f"Bearer {token}", 
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        res = urllib.request.urlopen(req).read()
        return json.loads(res)
    except:
        return []

def count_recent_events(events, cutoff_date):
    count = 0
    for ev in events:
        # Check all possible date fields across the different endpoints
        date_str = (ev.get("Date") or ev.get("date") or 
                    ev.get("ReportDate") or ev.get("TransactionDate") or 
                    ev.get("action_date") or ev.get("pub_date"))
        if not date_str: continue
        try:
            # Handle timestamps (e.g., '2026-03-30 00:00:00' or '2026-03-30T12:00:00')
            clean_date_str = str(date_str).split("T")[0].split(" ")[0]
            t_date = datetime.strptime(clean_date_str, "%Y-%m-%d")
            if t_date >= cutoff_date:
                count += 1
        except:
            pass
    return count

def main():
    token = get_quiver_token()
    if not token:
        print("No Quiver token found.")
        return

    print("Fetching Watchlist...")
    watchlist_data = run_gog(f'get {LIVE_SHEET_ID} "Watchlist!A:K" --json')
    if not watchlist_data or "values" not in watchlist_data:
        print("Failed to fetch watchlist.")
        return

    rows = watchlist_data["values"]
    
    if not rows or len(rows) == 0:
        print("Watchlist is empty.")
        return
        
    # Ensure header for Column J exists
    header_row = rows[0]
    while len(header_row) < 10:
        header_row.append("")
    if header_row[9] != "Quiver Conviction":
        run_gog(f"update {LIVE_SHEET_ID} \"Watchlist!J1\" --values-json '[[\"Quiver Conviction\"]]' --input USER_ENTERED")

    ninety_days_ago = datetime.now() - timedelta(days=90)
    fourteen_days_ago = datetime.now() - timedelta(days=14)
    one_twenty_days_ago = datetime.now() - timedelta(days=120)
    
    shield_data = {}

    for i, row in enumerate(rows):
        if i == 0 or not row: continue
        ticker = row[0].strip()
        if not ticker or ticker == "CASH": continue
        
        row_idx = i + 1
        print(f"\nAnalyzing {ticker}...")
        
        trades = get_congress_trades(ticker, token)
        dpi = get_dpi(ticker)
        
        # New Catalyst Fetches
        contracts = get_quiver_beta("govcontracts", ticker, token)
        lobbying = get_quiver_beta("lobbying", ticker, token)
        patents = get_quiver_beta("allpatents", ticker, token)
        
        # Apply specific lookback windows per dataset
        recent_contracts = count_recent_events(contracts, ninety_days_ago) # 90 days
        recent_lobbying = count_recent_events(lobbying, one_twenty_days_ago) # 120 days
        recent_patents = count_recent_events(patents, fourteen_days_ago) # 14 days
        
        cat_contracts = recent_contracts * 10
        cat_lobbying = recent_lobbying * 5
        cat_patents = min(recent_patents * 2, 10) # Cap at 10 points
        
        catalyst_score = cat_contracts + cat_lobbying + cat_patents
        
        score = 50
        buys = 0
        sells = 0
        latest_action = "No activity in 90d"
        
        for trade in trades:
            date_str = trade.get("ReportDate") or trade.get("TransactionDate")
            if not date_str: continue
            
            try:
                t_date = datetime.strptime(date_str.split("T")[0], "%Y-%m-%d")
            except:
                continue
                
            if t_date >= ninety_days_ago:
                action = trade.get("Transaction", "")
                
                # --- PATCH: Robust Amount Parsing ---
                raw_amount = str(trade.get("Amount", "0"))
                clean_amount = raw_amount.replace('$', '').replace(',', '')
                if '-' in clean_amount:
                    clean_amount = clean_amount.split('-')[0].strip()
                
                try:
                    amount = float(clean_amount)
                except ValueError:
                    amount = 0.0
                # ------------------------------------
                
                rep = trade.get("Representative", "Unknown")
                
                points = 2
                if amount >= 100000: points = 15
                elif amount >= 50000: points = 10
                elif amount >= 15000: points = 5
                
                if "Purchase" in action:
                    score += points
                    buys += 1
                    if buys == 1: latest_action = f"Bought by {rep}"
                elif "Sale" in action:
                    score -= points
                    sells += 1
                    if sells == 1: latest_action = f"Sold by {rep}"
                    
        score = max(0, min(100, score)) # Cap between 0 and 100
        
        if buys > 0 or sells > 0:
            macro_reasoning = f"Buys: {buys}, Sells: {sells} ({latest_action})"
        else:
            macro_reasoning = f"Neutral: No recent activity"
            
        reasoning = f"{macro_reasoning} | DPI: {dpi:.2f} | Catalysts: {catalyst_score}pts (C:{recent_contracts} L:{recent_lobbying} P:{recent_patents})"
        display_text = f"Macro: {score} | Catalyst: {catalyst_score} | Buys: {buys}, Sells: {sells} | DPI: {dpi:.2f}"
        
        shield_data[ticker] = {
            "score": score,
            "catalyst_score": catalyst_score,
            "recent_contracts": recent_contracts,
            "recent_lobbying": recent_lobbying,
            "recent_patents": recent_patents,
            "reasoning": reasoning,
            "dpi": dpi,
            "last_updated": datetime.now().isoformat()
        }
        
        print(f"  Macro Score: {score} | Catalyst Score: {catalyst_score} (C:{recent_contracts} L:{recent_lobbying} P:{recent_patents})")
        
        payload = [[display_text]]
        safe_payload = json.dumps(payload)
        run_gog(f"update {LIVE_SHEET_ID} \"Watchlist!J{row_idx}\" --values-json '{safe_payload}' --input USER_ENTERED")
        
        # --- PATCH: API Throttling ---
        time.sleep(1.0)

    # Save local JSON cache for Sniper
    os.makedirs(os.path.dirname(SHIELD_FILE), exist_ok=True)
    with open(SHIELD_FILE, 'w') as f:
        json.dump(shield_data, f, indent=2)
        
    print("\nQuiver Shield successfully built and synchronized!")

if __name__ == "__main__":
    main()