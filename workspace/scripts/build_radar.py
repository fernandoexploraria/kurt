import json
import subprocess
import os
from datetime import datetime
import time
import requests
import tempfile

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
LOSER_LEASH_MIN_PCT = 0.05 # Tightest leash permitted (-5%)
LOSER_LEASH_MAX_PCT = 0.08 # Widest leash permitted (-8%)
LOSER_LEASH_ATR_FACTOR = 1.5 # Sizing multiplier for the leash percentage
LOW_BETA_MULTIPLIER = 4.0 # Defensive trailing-stop multiplier for low-beta assets [P2-7]

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except: pass
    return {}

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
                    save_json_atomic(cache, CACHE_FILE)
                try:
                    return float(res["data"]["data"]["beta_1_year"])
                except:
                    return None
        except: pass
        time.sleep(0.3)
    return None

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
        if beta is None:
            if ticker in radar:
                print(f"  [⚠️] WARNING: Beta fetch failed for {ticker}. API dropout detected.")
                print(f"  Carrying forward yesterday's trailing floor (${radar[ticker].get('current_floor')}) to protect capital.")
                new_radar[ticker] = radar[ticker]
                
                old_drop = radar[ticker].get("trailing_drop_amount", 1.0)
                run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!L{row_num}' --values-json '[[{old_drop}]]' --input USER_ENTERED")
                continue
            else:
                print(f"  [!] New position {ticker} beta fetch failed. Defaulting to 1.05 to force-active safety stop.")
                beta = 1.05

        print(f"  [+] Beta: {beta:.2f}")
        
        if beta is not None:
            current_price = pos["price"]
            atr = pos["atr"]
            avg_cost = pos.get("avg_cost", 0.0)
            shares = pos.get("shares", 1)
            
            # --- THE LOSER LEASH ---
            # If the stock is underwater, revoke the ATR buffer and enforce a dynamic leash [P2-4]
            if avg_cost > 0 and current_price < avg_cost:
                print(f"  [!] LOSER LEASH ACTIVATED: Trading below entry (${avg_cost:.2f}).")
                
                atr_pct = atr / avg_cost
                leash_pct = max(LOSER_LEASH_MIN_PCT, min(LOSER_LEASH_MAX_PCT, LOSER_LEASH_ATR_FACTOR * atr_pct))
                
                highest = avg_cost
                current_floor = round(avg_cost * (1.0 - leash_pct), 2)
                drop_amount = round(highest - current_floor, 2)
                display_multiplier = f"N/A (Dynamic Leash: {leash_pct * 100.0:.1f}%)"
            else:
                # Standard profit-protecting trailing stop
                # Determine multiplier based on high-beta vs. low-beta asset class [P2-7]
                if beta >= BETA_THRESHOLD:
                    multiplier = optimized_multipliers.get(ticker, DEFAULT_ATR_MULTIPLIER)
                    display_multiplier = f"{multiplier}x ATR (High-Beta)"
                else:
                    multiplier = LOW_BETA_MULTIPLIER
                    display_multiplier = f"{multiplier}x ATR (Low-Beta Defensive)"
                    
                drop_amount = round(atr * multiplier, 2)
                
                # Carry over the highest seen price to maintain ratchet memory
                highest = current_price
                if ticker in radar:
                    highest = max(current_price, radar[ticker].get("highest_seen_price", 0.0))
                    
                current_floor = round(highest - drop_amount, 2)

            # --- THE MONOTONE RATCHET CONSTRAINT ---
            # Enforce that the trailing stop-loss floor can only ever move UP, never down. [P2-6]
            if ticker in radar:
                yesterday_floor = radar[ticker].get("current_floor", 0.0)
                if yesterday_floor > current_floor:
                    print(f"  [🛡️] RATCHET LOCKED: Keeping yesterday's higher floor (${yesterday_floor:.2f}) instead of theoretical (${current_floor:.2f})")
                    current_floor = yesterday_floor
            
            new_radar[ticker] = {
                "beta": round(beta, 2),
                "highest_seen_price": highest,
                "trailing_drop_amount": drop_amount,
                "current_floor": current_floor,
                "shares": shares,
                "last_updated": datetime.now().isoformat()
            }
            print(f"  [+] RADAR UPDATED. Highest: ${highest} | Drop: ${drop_amount} ({display_multiplier}) | Floor: ${current_floor}")
            
            # --- UNIFY SHEET AND RADAR FLOORS [P2-6] ---
            # Update the human dashboard: K gets the canonical floor, L gets the drop length
            run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!K{row_num}' --values-json '[[\"{current_floor}\"]]' --input USER_ENTERED")
            run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!L{row_num}' --values-json '[[\"{drop_amount}\"]]' --input USER_ENTERED")
            
    save_json_atomic(new_radar, RADAR_FILE)
    print(f"\nRadar build complete. {len(new_radar)} high-beta targets saved to {RADAR_FILE}.")

if __name__ == "__main__":
    main()