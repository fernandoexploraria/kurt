import json
import subprocess
import os
import requests
from datetime import datetime

# --- CONFIGURATION ---
STATE_FILE = "/root/.openclaw/workspace/memory/snag_state.json"
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

# --- BATCH PRICE FETCHING ---
def get_batch_prices(symbols):
    """Fetches real-time prices for a list of symbols in chunks of 10."""
    prices = {}
    
    # TradingView batch endpoint allows max 10 symbols per request
    chunk_size = 10
    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Content-Type": "application/json"
    }
    
    for chunk in chunks:
        # Standardize prefixes for TV (assuming most are major US, TV handles raw tickers well in batch but safer to add NASDAQ/NYSE if known. 
        # For simplicity in the batch, we will prefix common ones or just rely on TV's resolution if we send them as is).
        # Actually, the batch API prefers EXCHANGE:TICKER format. 
        # Since we don't have exchange info in the sheet easily, we'll try raw. If it fails, we fall back.
        # Let's format them generically. RapidAPI usually handles raw tickers well on the quote endpoint.
        
        payload = {
            "symbols": chunk,
            "fields": "lp", # We only need the Last Price
            "session": "regular"
        }
        
        url = f"https://{RAPIDAPI_HOST}/api/quote/batch"
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            data = response.json()
            if data.get("success"):
                results = data.get("data", {}).get("data", [])
                for res in results:
                    if res.get("success"):
                        # TV returns the symbol back like "NASDAQ:AAPL", we need to map it back
                        symbol_resp = res.get("symbol", "")
                        raw_ticker = symbol_resp.split(":")[-1] if ":" in symbol_resp else symbol_resp
                        lp = res.get("data", {}).get("lp")
                        if lp:
                            prices[raw_ticker] = lp
        except Exception:
            pass
            
    return prices

def main():
    alert_state = load_and_clean_state()

    env = os.environ.copy()
    env["GOG_ACCOUNT"] = "fernando@exploraria.ai"
    res = subprocess.run(
        ["/usr/local/bin/gog", "sheets", "get", "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I", "Watchlist!A:D", "--json"], 
        env=env, 
        capture_output=True, text=True
    )

    if res.returncode != 0:
        print("NO_REPLY")
        exit(0)

    try:
        data = json.loads(res.stdout)
    except Exception:
        print("NO_REPLY")
        exit(0)

    # 1. Build the list of tickers and targets
    watchlist_targets = {}
    for row in data.get("values", [])[1:]:
        if len(row) >= 4 and row[0].strip():
            raw_ticker = row[0].strip()
            
            # Note: We no longer skip based on alert_state here because a stock 
            # might have a warning alert but still need its snag alert evaluated.
            try:
                entry = float(str(row[3]).replace("$", "").replace(",", ""))
                if entry > 0:
                    watchlist_targets[raw_ticker] = entry
            except Exception:
                pass

    if not watchlist_targets:
        print("NO_REPLY")
        exit(0)

    # 2. Fetch all live prices in bulk
    symbols_to_fetch = list(watchlist_targets.keys())
    
    # TV Batch API strictly requires Exchange:Symbol format (e.g., NASDAQ:AAPL) for reliable results.
    # Since we don't store exchanges in our sheet, we will quickly map the major ones, and default to NASDAQ/NYSE.
    # To keep it completely bulletproof and fast, we will append multiple common prefixes to the search block so TV definitely finds them.
    # TV Batch will just return "failed" for the wrong prefixes and "success" for the right ones in the same chunk.
    formatted_symbols = []
    for t in symbols_to_fetch:
        formatted_symbols.extend([f"NASDAQ:{t}", f"NYSE:{t}", f"AMEX:{t}", f"CRYPTO:{t}"])

    live_prices = get_batch_prices(formatted_symbols)

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
        print("🚨 **MASTER SNAG SCANNER ALERT** 🚨\\nThe following Watchlist items have triggered an alert:\\n")
        print("\\n".join(triggered))
    else:
        print("NO_REPLY")

if __name__ == "__main__":
    main()