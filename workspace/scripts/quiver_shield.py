import json
import subprocess
import os
import urllib.request
import time
from datetime import datetime, timedelta
import tempfile

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"

SALE_WEIGHT = 0.5 # Asymmetric penalty multiplier for Congressional sales

def save_json_atomic(data, path):
    """
    UPGRADE P0-6: Writes a JSON state file atomically using a temporary file
    and os.replace to prevent file corruption during mid-write crashes.
    """
    dir_name = os.path.dirname(path)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)

    # 1. Create a secure temp file in the same target folder
    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False) as tf:
        temp_path = tf.name
        json.dump(data, tf, indent=2)
        tf.flush()
        # 2. Force the OS to physically write the buffers to the storage drive
        os.fsync(tf.fileno())

    # Ensure the file is readable by other processes before replacing
    os.chmod(temp_path, 0o644)
    # 3. Perform an atomic replace of the old file with the complete new file
    os.replace(temp_path, path)

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

import urllib.error

def fetch_with_retry(url, headers, max_retries=4):
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            res = urllib.request.urlopen(req).read()
            return json.loads(res)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = 2 ** attempt  # 1s, 2s, 4s, 8s
                print(f"    Rate limited (429) on {url.split('/')[-2]}/{url.split('/')[-1]}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"    HTTP Error {e.code} for {url.split('/')[-2]}/{url.split('/')[-1]}")
                return []
        except Exception as e:
            print(f"    Error fetching Quiver data: {e}")
            return []
    print(f"    Failed after {max_retries} attempts for {url.split('/')[-2]}/{url.split('/')[-1]}")
    return []

def get_congress_trades(ticker, token):
    url = f"https://api.quiverquant.com/beta/historical/congresstrading/{ticker}"
    headers = {
        "Authorization": f"Bearer {token}", 
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    return fetch_with_retry(url, headers)

def get_quiver_beta(endpoint, ticker, token):
    url = f"https://api.quiverquant.com/beta/historical/{endpoint}/{ticker}"
    headers = {
        "Authorization": f"Bearer {token}", 
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    return fetch_with_retry(url, headers)

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

def novelty_points(events, window_days, per_event_points, cap, cutoff_date):
    """
    UPGRADE P2-2: Calculates catalyst points by measuring recent activity
    against a 1-year historical baseline to isolate true "new news" or novelty.
    """
    now = datetime.now()
    one_year_ago = now - timedelta(days=365)

    # 1. Count events in the short-term window and the full year
    recent = count_recent_events(events, cutoff_date)
    year_total = count_recent_events(events, one_year_ago)

    # 2. Calculate the expected baseline frequency for the target window
    baseline = year_total * (window_days / 365.0)

    # 3. Isolate excess "novel" activity above the baseline
    excess = max(0.0, recent - baseline)

    # 4. Compute and cap the final score points
    points = min(excess * per_event_points, cap)
    return round(points, 2), recent

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
        time.sleep(0.5)
        
        dpi = get_dpi(ticker)
        time.sleep(0.5)

        # Sort trades descending by date to ensure the newest trade is processed first [P2-8]
        def get_trade_date(t):
            d_str = t.get("ReportDate") or t.get("TransactionDate") or "1970-01-01"
            return d_str.split("T")[0]

        if trades:
            trades = sorted(trades, key=get_trade_date, reverse=True)
        
        # New Catalyst Fetches
        contracts = get_quiver_beta("govcontracts", ticker, token)
        time.sleep(0.5)
        
        lobbying = get_quiver_beta("lobbying", ticker, token)
        time.sleep(0.5)
        
        patents = get_quiver_beta("allpatents", ticker, token)
        
        # Calculate novelty-normalized catalyst points and extract raw counts [P2-2]
        cat_contracts, recent_contracts = novelty_points(contracts, 90, 10, 50, ninety_days_ago)
        cat_lobbying, recent_lobbying = novelty_points(lobbying, 120, 5, 25, one_twenty_days_ago)
        cat_patents, recent_patents = novelty_points(patents, 14, 2, 10, fourteen_days_ago)
        
        catalyst_score = round(cat_contracts + cat_lobbying + cat_patents, 2)
        
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
                    # Apply asymmetric 0.5x sale weight [P2-1]
                    score -= (points * SALE_WEIGHT)
                    sells += 1
                    if sells == 1: latest_action = f"Sold by {rep}"
                    
        # Round the final score to 1 decimal place after applying float multipliers
        score = round(max(0, min(100, score)), 1)
        
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
    save_json_atomic(shield_data, SHIELD_FILE)
        
    print("\nQuiver Shield successfully built and synchronized!")

if __name__ == "__main__":
    main()