import json
import subprocess
import os
from datetime import datetime
import time
import requests

SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"

# --- CONFIGURATION ---
TEST_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") # EXACT KEY FROM SCREENSHOT
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"
ORDERS_FILE = "/root/.openclaw/workspace/memory/pending_orders.json"

DEFAULT_ATR_MULTIPLIER = 3.0 # Standardized fallback multiplier for unoptimized assets [P0-5]

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def load_shield():
    if os.path.exists(SHIELD_FILE):
        try:
            with open(SHIELD_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {}

def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'w') as f:
        json.dump(cache, f, indent=2)

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
    tickers = []
    for i, row in enumerate(data.get('values', [])):
        if len(row) > 0 and row[0].strip() and row[0].strip().upper() not in ["CASH", "TICKER"]:
            price = 0.0
            current_floor = 0.0
            if len(row) > 3 and row[3].strip():
                try: price = float(str(row[3]).replace(',', '').replace('$', ''))
                except: pass
            if len(row) > 10 and row[10].strip():
                try: current_floor = float(str(row[10]).replace(',', '').replace('$', ''))
                except: pass
            tickers.append({
                "row": i + 4, 
                "ticker": row[0].strip(),
                "price": price,
                "floor": current_floor
            })
    return tickers

def fetch_atr(ticker, cache):
    """Calculates 14-Day ATR dynamically on the fly."""
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/price/{prefix}{ticker}?range=16&timeframe=D"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                hist = None
                if "data" in res and "history" in res["data"]:
                    hist = res["data"]["history"]
                elif "history" in res:
                    hist = res["history"]
                    
                if hist and len(hist) > 1:
                    trs = []
                    for j in range(1, len(hist)):
                        h = hist[j]["max"]
                        l = hist[j]["min"]
                        pc = hist[j-1]["close"]
                        tr = max(h - l, abs(h - pc), abs(l - pc))
                        trs.append(tr)
                    return sum(trs[-14:]) / min(14, len(trs))
        except: pass
        time.sleep(0.3)
    return 0.0

def get_analyst_target(ticker, cache):
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/market-data/{prefix}{ticker}/analyst-recommendations"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                return res["data"]["analyst_recommendations"].get("price_target_average")
        except: pass
        time.sleep(0.3)
    return None

def get_ta_ceiling(ticker, cache):
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
    if ticker in cache:
        prefixes = [cache[ticker]] + [p for p in prefixes if p != cache[ticker]]

    headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/ta/{prefix}{ticker}/indicators"
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            if res.get("success"):
                if ticker not in cache or cache[ticker] != prefix:
                    cache[ticker] = prefix
                    save_cache(cache)
                ind = res["data"]
                if ind.get("Pivot.M.Fibonacci.R1"): return ind["Pivot.M.Fibonacci.R1"]
                elif ind.get("Pivot.M.Classic.R1"): return ind["Pivot.M.Classic.R1"]
        except: pass
        time.sleep(0.3)
    return None

def get_quiver_adjustments(ticker, shield_cache):
    modifier = 1.0 
    shield_data = shield_cache.get(ticker, {})
    
    # 1. Dark Pool Index (DPI) Adjustments
    latest_dpi = shield_data.get("dpi", 0.5)
    if latest_dpi > 0.50:
        reduction = (latest_dpi - 0.50) * 0.2
        modifier -= min(reduction, 0.05)

    # 2. Congressional Conviction Score Adjustments
    score = shield_data.get("score", 50)
    if score != 50:
        boost = (score - 50) * 0.002
        modifier += boost
        
    return modifier

def update_sheet(row, target_price, floor_price, atr_val):
    today = datetime.now().strftime("%Y-%m-%d")
    run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!G{row}' --values-json '[[{target_price}]]' --input USER_ENTERED")
    run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!H{row}' --values-json '[[\"=(G{row}-D{row})/D{row}\"]]' --input USER_ENTERED")
    run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!I{row}' --values-json '[[\"{today}\"]]' --input USER_ENTERED")
    run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!J{row}' --values-json '[[{atr_val}]]' --input USER_ENTERED")
    run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Positions!K{row}' --values-json '[[{floor_price}]]' --input USER_ENTERED")

def sync_pending_orders(results):
    if not os.path.exists(ORDERS_FILE):
        return
    try:
        with open(ORDERS_FILE, 'r') as f:
            orders = json.load(f)
    except:
        return
        
    updated = False
    print("\n=== SYNCHRONIZING EXIT TRAPS (SELL / STOP_LOSS) ===")
    
    target_lookup = {r['ticker']: r['target'] for r in results}
    floor_lookup = {r['ticker']: r['floor'] for r in results}

    for ticker, data in orders.items():
        if data.get("status") == "waiting":
            if data.get("action") == "SELL" and ticker in target_lookup:
                old_price = data.get("target_price")
                new_price = target_lookup[ticker]
                if old_price != new_price:
                    print(f"  [Sync] Updating {ticker} TAKE_PROFIT Trap: ${old_price} -> ${new_price}")
                    data["target_price"] = new_price
                    updated = True
            elif data.get("action") == "STOP_LOSS" and ticker in floor_lookup:
                old_price = data.get("target_price")
                new_price = floor_lookup[ticker]
                if old_price != new_price:
                    print(f"  [Sync] Updating {ticker} STOP_LOSS Trap: ${old_price} -> ${new_price}")
                    data["target_price"] = new_price
                    updated = True
                    
    if updated:
        with open(ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=2)
        print("  [✓] All exit traps synchronized successfully.")
    else:
        print("  [-] No waiting exit traps required updating.")

def main():
    print("Starting UNIFIED V2.1 Target Calibration (Dynamic ATR, Ceilings, and Ratchet Floors)...")
    cache = load_cache()
    shield_cache = load_shield()
    
    # Load Optimized Multipliers
    optimized_file = "/root/.openclaw/workspace/memory/optimized_multipliers.json"
    optimized_multipliers = {}
    if os.path.exists(optimized_file):
        try:
            with open(optimized_file, 'r') as f:
                optimized_multipliers = json.load(f)
        except: pass

    tickers = get_positions()
    results = []

    for item in tickers:
        ticker, row, current_price, current_floor = item['ticker'], item['row'], item['price'], item['floor']
        print(f"\n--- {ticker} ---")
        
        # 1. Calculate fresh ATR on the fly
        raw_atr = fetch_atr(ticker, cache)
        atr = round(raw_atr, 2)
        print(f"  [+] Calculated LIVE ATR: ${atr}")
        
        if atr <= 0:
            atr = 1.0
            
        # 2. Find Ceiling
        base_ceiling = get_analyst_target(ticker, cache)
        if base_ceiling: print(f"  [+] Found Analyst Ceiling: ${base_ceiling}")
        else:
            base_ceiling = get_ta_ceiling(ticker, cache)
            if base_ceiling: print(f"  [+] Found TA Ceiling: ${base_ceiling}")
        
        if not base_ceiling:
            print("  [!] No Analyst/TA Ceiling. Using 3x ATR Fallback.")
            base_ceiling = current_price + (3 * atr)
            
        modifier = get_quiver_adjustments(ticker, shield_cache)
        print(f"  [+] Scaled Quiver Modifier: {modifier:.3f}")
        
        target_price = round(base_ceiling * modifier, 2)
        
        # DYNAMIC SANITY CHECK
        if current_price > 0 and target_price < (current_price + atr):
            print(f"  [!] Sanity Check: Target too tight. Setting to +1 ATR (${current_price + atr}).")
            target_price = round(current_price + atr, 2)
            
        # PHASE 2: THE RATCHET FLOOR LOGIC
        # Unified fallback multiplier to prevent dashboard and radar stop-loss drift [P0-5]
        multiplier = optimized_multipliers.get(ticker, DEFAULT_ATR_MULTIPLIER)
        print(f"  [+] Optimizer Brain: Using {multiplier}x ATR multiplier for Floor.")
        theoretical_floor = round(current_price - (multiplier * atr), 2)
        if theoretical_floor < 0: theoretical_floor = 0.0
        
        if current_floor == 0.0 or theoretical_floor > current_floor:
            final_floor = theoretical_floor
            print(f"  [+] Floor Ratcheted UP: ${final_floor} (was ${current_floor})")
        else:
            final_floor = current_floor
            print(f"  [+] Floor Locked: ${final_floor} (theoretical was ${theoretical_floor})")
            
        print(f"  [=] FINAL CEILING: ${target_price} | FINAL FLOOR: ${final_floor}")
        
        # 4. Write Target, Formula, Date, ATR, and Floor back to spreadsheet
        update_sheet(row, target_price, final_floor, atr)
        
        pct_to_target = ((target_price - current_price) / current_price) * 100 if current_price > 0 else 999
        results.append({"ticker": ticker, "target": target_price, "floor": final_floor, "pct": pct_to_target})

    print("\n=== SUMMARY (HARVEST & FLOORS) ===")
    results.sort(key=lambda x: x['pct'])
    for r in results: print(f"{r['ticker']} - Ceiling: ${r['target']} | Floor: ${r['floor']}")

    sync_pending_orders(results)

if __name__ == "__main__":
    main()
