import json
import subprocess
import os
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
STATE_FILE = "/root/.openclaw/workspace/memory/snag_state.json"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"
TODAY = datetime.now().strftime("%Y-%m-%d")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") # Verified Working Key
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

# --- STATE MANAGEMENT ---
def load_and_clean_state():
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                raw_state = json.load(f)
                for ticker, date_alerted in raw_state.items():
                    if date_alerted == TODAY:
                        state[ticker] = date_alerted
        except Exception:
            pass
    return state

def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# --- PARALLEL BATCH PRICE FETCHING ---
def fetch_chunk(chunk, headers):
    payload = {
        "symbols": chunk,
        "fields": "lp",
        "session": "regular"
    }
    url = f"https://{RAPIDAPI_HOST}/api/quote/batch"
    import time
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    return data.get("data", {}).get("data", [])
        except Exception:
            pass
        if attempt < 2:
            time.sleep(1)
    return []

def get_batch_prices_parallel(symbols):
    """Fetches real-time prices for a list of symbols in chunks of 10 in parallel."""
    prices = {}
    if not symbols:
        return prices

    chunk_size = 10
    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Content-Type": "application/json"
    }

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_chunk, chunk, headers): chunk for chunk in chunks}
        for future in as_completed(futures):
            results = future.result()
            for res in results:
                if res.get("success"):
                    symbol_resp = res.get("symbol", "")
                    raw_ticker = symbol_resp.split(":")[-1] if ":" in symbol_resp else symbol_resp
                    lp = res.get("data", {}).get("lp")
                    if lp:
                        prices[raw_ticker] = lp
    return prices

def get_all_prices(symbols):
    """Prefixes symbols using the exchange_cache.json, then fetches them in parallel chunks."""
    cache = {}
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                cache = json.load(f)
        except Exception:
            pass

    prefixed_symbols = []
    for s in symbols:
        prefix = cache.get(s, "NASDAQ:")
        prefixed_symbols.append(f"{prefix}{s}")
        
    return get_batch_prices_parallel(prefixed_symbols)

def main():
    alert_state = load_and_clean_state()

    env = os.environ.copy()
    env["GOG_ACCOUNT"] = "fernando@exploraria.ai"
    import time
    
    res = None
    data = None
    
    # Strictly use the main Simulator V3 sheet, protected by timeouts and no-input
    for attempt in range(3):
        try:
            res = subprocess.run(
                ["/usr/local/bin/gog", "sheets", "get", "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I", "Watchlist!A:D", "--json", "--no-input"], 
                env=env, 
                capture_output=True, text=True,
                timeout=12
            )
            if res.returncode == 0:
                data = json.loads(res.stdout)
                break
        except Exception:
            pass
            
        if attempt < 2:
            time.sleep(2)

    if not data:
        print("NO_REPLY")
        exit(0)

    # 1. Build the list of tickers and targets
    watchlist_targets = {}
    for row in data.get("values", [])[1:]:
        if len(row) >= 4 and row[0].strip():
            raw_ticker = row[0].strip()
            try:
                entry = float(str(row[3]).replace("$", "").replace(",", ""))
                if entry > 0:
                    watchlist_targets[raw_ticker] = entry
            except Exception:
                pass

    if not watchlist_targets:
        print("NO_REPLY")
        exit(0)

    # 2. Fetch all live prices in parallel with fallback resolution
    symbols_to_fetch = list(watchlist_targets.keys())
    live_prices = get_all_prices(symbols_to_fetch)

    # 3. Compare and trigger
    triggered = []
    new_alerts_fired = False

    for ticker, entry_price in watchlist_targets.items():
        curr_price = live_prices.get(ticker)
        
        # If batch API failed to resolve it, we just skip it for this 30-min window
        if curr_price:
            snag_key = f"{ticker}_snag"
            warn_key = f"{ticker}_warning"
            
            # TRIGGER 1: Target Entry Hit
            if curr_price <= entry_price:
                if snag_key not in alert_state:
                    triggered.append(f"🛍️ **SNAG TARGET HIT**: {ticker} dropped to ${curr_price:.2f} (Target: ${entry_price:.2f}). BUY EXECUTION IMMINENT.")
                    alert_state[snag_key] = TODAY
                    new_alerts_fired = True
                    
            # TRIGGER 2: Approaching Warning (Within 3% of Entry)
            else:
                pct_away = ((curr_price - entry_price) / entry_price) * 100
                if pct_away <= 3.0:
                    if warn_key not in alert_state:
                        triggered.append(f"👀 **APPROACHING SNAG**: {ticker} is at ${curr_price:.2f} (Just {pct_away:.2f}% away from ${entry_price:.2f} target).")
                        alert_state[warn_key] = TODAY
                        new_alerts_fired = True

    if new_alerts_fired or len(alert_state) > 0:
        save_state(alert_state)

    if triggered:
        print("🚨 **MASTER SNAG SCANNER ALERT** 🚨\nThe following Watchlist items have triggered an alert:\n")
        print("\n".join(triggered))
    else:
        print("NO_REPLY")

if __name__ == "__main__":
    main()