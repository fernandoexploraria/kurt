import json
import subprocess
import os
import requests
from datetime import datetime

# --- CONFIGURATION ---
STATE_FILE = "/root/.openclaw/workspace/memory/target_state.json"
TODAY = datetime.now().strftime("%Y-%m-%d")
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

# --- STATE MANAGEMENT ---
def load_and_clean_state():
    """Loads the state file and purges any alerts from previous days."""
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                raw_state = json.load(f)
                for key, date_alerted in raw_state.items():
                    if date_alerted == TODAY:
                        state[key] = date_alerted
        except Exception:
            pass
    return state

def save_state(state):
    """Saves the active state dictionary back to the JSON file."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# --- BATCH PRICE FETCHING ---
def get_batch_prices(symbols):
    """Fetches real-time prices for a list of symbols in chunks of 10."""
    prices = {}
    
    chunk_size = 10
    chunks = [symbols[i:i + chunk_size] for i in range(0, len(symbols), chunk_size)]
    
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST,
        "Content-Type": "application/json"
    }
    
    for chunk in chunks:
        payload = {
            "symbols": chunk,
            "fields": "lp", 
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
                        symbol_resp = res.get("symbol", "")
                        raw_ticker = symbol_resp.split(":")[-1] if ":" in symbol_resp else symbol_resp
                        lp = res.get("data", {}).get("lp")
                        if lp:
                            prices[raw_ticker] = lp
        except Exception:
            pass
            
    return prices

def main():
    env = os.environ.copy()
    env["GOG_ACCOUNT"] = "fernando@exploraria.ai"
    
    alert_state = load_and_clean_state()

    # 2. Fetch live Positions from Google Sheets (A through K captures Ticker, Target Price, and Floor Price)
    res = subprocess.run(
        ["/usr/local/bin/gog", "sheets", "get", "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I", "Positions!A4:K50", "--json"], 
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

    # 3. Build the list of tickers and target ceilings/floors
    position_targets = {}
    for row in data.get("values", []):
        if len(row) >= 7 and row[0].strip() and row[0].strip().upper() not in ["CASH", "TICKER"]:
            raw_ticker = row[0].strip()
            
            target = 0.0
            try:
                target = float(str(row[6]).replace("$", "").replace(",", "").replace("[", "").replace("]", ""))
            except Exception:
                pass
                
            floor = 0.0
            if len(row) >= 11:
                try:
                    floor = float(str(row[10]).replace("$", "").replace(",", "").replace("[", "").replace("]", ""))
                except Exception:
                    pass

            if target > 0 or floor > 0:
                position_targets[raw_ticker] = {"ceiling": target, "floor": floor}

    if not position_targets:
        print("NO_REPLY")
        exit(0)

    # 4. Fetch all live prices in bulk
    symbols_to_fetch = list(position_targets.keys())
    formatted_symbols = []
    for t in symbols_to_fetch:
        formatted_symbols.extend([f"NASDAQ:{t}", f"NYSE:{t}", f"AMEX:{t}", f"CRYPTO:{t}"])

    live_prices = get_batch_prices(formatted_symbols)

    # 5. Compare and trigger the Harvest Zones and Stop-Loss Floors
    triggered = []
    new_alerts_fired = False

    for ticker, bounds in position_targets.items():
        curr_price = live_prices.get(ticker)
        
        if curr_price:
            ceiling = bounds["ceiling"]
            floor = bounds["floor"]
            
            ceiling_key = f"{ticker}_ceiling"
            harvest_key = f"{ticker}_harvest"
            floor_key = f"{ticker}_floor"
            
            # TRIGGER 1: Hard Stop Broken (Floor)
            if floor > 0 and curr_price <= floor:
                if floor_key not in alert_state:
                    triggered.append(f"🚨 **HARD STOP BROKEN**: {ticker} crashed to ${curr_price:.2f}! Liquidate. (Floor was ${floor:.2f})")
                    alert_state[floor_key] = TODAY
                    new_alerts_fired = True
                    
            # TRIGGER 2: Absolute Target Hit (Ceiling)
            if ceiling > 0:
                pct_away = ((ceiling - curr_price) / curr_price) * 100
                if curr_price >= ceiling:
                    if ceiling_key not in alert_state:
                        triggered.append(f"🎯 **TARGET HIT**: {ticker} is at ${curr_price:.2f} (Target: ${ceiling:.2f}). SELL EXECUTION REQUIRED.")
                        alert_state[ceiling_key] = TODAY
                        new_alerts_fired = True
                
                # TRIGGER 3: Harvest Zone Warning (Within 5% of Ceiling)
                elif pct_away <= 5.0 and pct_away > 0:
                    if harvest_key not in alert_state:
                        triggered.append(f"🍎 **HARVEST ZONE**: {ticker} is at ${curr_price:.2f} (Just {pct_away:.2f}% away from ${ceiling:.2f} target).")
                        alert_state[harvest_key] = TODAY
                        new_alerts_fired = True

    # 6. Save state only if we have active alerts to track
    if new_alerts_fired or len(alert_state) > 0:
        save_state(alert_state)

    # 7. Output strictly formatted for the LLM
    if triggered:
        print("🚨 **MASTER TARGET SCANNER ALERT** 🚨\\nThe following Positions have triggered an alert:\\n")
        print("\\n".join(triggered))
    else:
        print("NO_REPLY")

if __name__ == "__main__":
    main()
