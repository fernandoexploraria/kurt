import json
import subprocess
import os
from datetime import datetime
import time
import requests

# --- CONFIGURATION ---
TEST_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") 
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"
RADAR_FILE = "/root/.openclaw/workspace/memory/trailing_radar.json"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"
OPTIMIZED_FILE = "/root/.openclaw/workspace/memory/optimized_multipliers.json"

BETA_THRESHOLD = 1.05 # Any stock with a Beta >= 1.05 gets a trailing stop
DEFAULT_ATR_MULTIPLIER = 3.0  # Fallback

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def save_json(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

def run_subprocess(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()

def get_positions():
    cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets get {TEST_SHEET_ID} 'Positions!A4:K50' --json"
    out = run_subprocess(cmd)
    if not out: return []
    data = json.loads(out)
    active = []
    for i, row in enumerate(data.get('values', [])):
        if len(row) >= 10:
            ticker = row[0].strip()
            if not ticker or ticker == "CASH" or ticker == "Ticker": continue
            try:
                shares = float(row[1].replace(',', ''))
                if shares > 0:
                    avg_cost = float(str(row[2]).replace(',', '').replace('$', '')) if len(row) > 2 and row[2] else 0.0
                    price = float(str(row[3]).replace(',', '').replace('$', '')) if len(row) > 3 and row[3] else 0.0
                    atr = float(str(row[9]).replace(',', '').replace('$', '')) if len(row) > 9 and row[9] else 1.0
                    active.append({"ticker": ticker, "avg_cost": avg_cost, "price": price, "atr": atr, "row": i + 4, "shares": shares})
            except Exception as e:
                pass
    return active

def get_beta(ticker, cache):
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/quote/{prefix}{ticker}?fields=beta_1_year"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_json(cache, CACHE_FILE)
                try:
                    return float(res["data"]["data"]["beta_1_year"])
                except:
                    return 0.0
        except: pass
        time.sleep(0.3)
    return 0.0

def main():
    print(f"[{datetime.now().isoformat()}] Building Trailing Radar...")
    cache = load_json(CACHE_FILE)
    radar = load_json(RADAR_FILE)
    optimized_multipliers = load_json(OPTIMIZED_FILE)
    positions = get_positions()
    
    new_radar = {}
    
    for pos in positions:
        ticker = pos["ticker"]
        row_num = pos["row"]
        beta = get_beta(ticker, cache)
        
        print(f"\n--- {ticker} ---")
        print(f"  [+] Beta: {beta:.2f}")
        
        if beta >= BETA_THRESHOLD:
            current_price = pos["price"]
            atr = pos["atr"]
            avg_cost = pos.get("avg_cost", 0.0)
            shares = pos.get("shares", 1)
            
            # --- THE LOSER LEASH ---
            # If the stock is underwater, revoke the massive ATR buffer and enforce a strict -8% hard floor from entry.
            if avg_cost > 0 and current_price < avg_cost:
                print(f"  [!] LOSER LEASH ACTIVATED: Trading below entry (${avg_cost:.2f}). Revoking ATR buffer.")
                highest = avg_cost
                current_floor = round(avg_cost * 0.92, 2)  # Strict max -8% loss
                drop_amount = round(highest - current_floor, 2)
            else:
                # Standard profit-protecting ATR buffer
                multiplier = optimized_multipliers.get(ticker, DEFAULT_ATR_MULTIPLIER)
                drop_amount = round(atr * multiplier, 2)
                
                # Carry over the highest price if it already exists in our active radar
                highest = current_price
                if ticker in radar:
                    highest = max(current_price, radar[ticker].get("highest_seen_price", 0.0))
                    
                current_floor = round(highest - drop_amount, 2)
            
            new_radar[ticker] = {
                "beta": round(beta, 2),
                "highest_seen_price": highest,
                "trailing_drop_amount": drop_amount,
                "current_floor": current_floor,
                "shares": shares,
                "last_updated": datetime.now().isoformat()
            }
            print(f"  [+] ADDED TO RADAR. Highest: ${highest} | Drop: ${drop_amount} ({multiplier}x ATR) | Floor: ${current_floor} | Shares: {shares}")
            
            # Update Dashboard (Column L) - Writing the Trailing Drop Amount (leash length) instead of the dynamic floor
            run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!L{row_num}' --values-json '[[{drop_amount}]]' --input USER_ENTERED")
        else:
            print(f"  [-] IGNORED. Beta is below {BETA_THRESHOLD} (Defensive/Low Volatility).")
            # Clear Dashboard if beta dropped and it was previously there
            run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!L{row_num}' --values-json '[[\"\"]]' --input USER_ENTERED")
            
    save_json(new_radar, RADAR_FILE)
    print(f"\nRadar build complete. {len(new_radar)} high-beta targets saved to {RADAR_FILE}.")

if __name__ == "__main__":
    main()