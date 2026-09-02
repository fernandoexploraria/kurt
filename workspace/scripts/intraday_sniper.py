import fcntl
import json
import os
import sys
import requests
from datetime import datetime

# Fetch the API key securely from the environment
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

RADAR_FILE = "/root/.openclaw/workspace/memory/trailing_radar.json"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"

def main():
    from datetime import datetime, time
    now = datetime.now().time()
    if not (time(7, 30) <= now <= time(13, 55)):
        print("NO_REPLY")
        return

    if not os.path.exists(RADAR_FILE):
        print("NO_REPLY")
        return
        
    with open(RADAR_FILE, 'r') as f:
        try:
            radar = json.load(f)
        except:
            print("NO_REPLY")
            return
            
    if not radar:
        print("NO_REPLY")
        return

    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            try:
                cache = json.load(f)
            except:
                pass

    # Load execution queue and history to reconcile active/completed trades
    queue_data = {}
    if os.path.exists("/root/.openclaw/workspace/memory/execution_queue.json"):
        try:
            with open("/root/.openclaw/workspace/memory/execution_queue.json", "r") as eq_f:
                queue_data = json.load(eq_f)
        except:
            pass

    history_data = {}
    if os.path.exists("/root/.openclaw/workspace/memory/execution_history.json"):
        try:
            with open("/root/.openclaw/workspace/memory/execution_history.json", "r") as h_f:
                history_data = json.load(h_f)
        except:
            pass

    def is_trade_active_or_completed(t):
        # Check if pending/processing in queue
        for oid, o in queue_data.items():
            if o.get("ticker") == t:
                return True
        # Check if completed/failed in history (looking for today's transactions)
        for oid, o in history_data.items():
            if o.get("ticker") == t and o.get("action") == "SELL" and "2026-09-01" in o.get("timestamp", ""):
                return True
        return False

    symbols_to_query = []
    updated_radar_pre = False
    
    for ticker, state in list(radar.items()):
        if state.get("status") == "breached":
            if is_trade_active_or_completed(ticker):
                continue  # Already alerted and active/completed, don't spam
            else:
                # Reconciliation trigger! Force re-arm because the trade is missing from queue/history.
                state.pop("status", None)
                state["last_updated"] = datetime.now().isoformat()
                updated_radar_pre = True
        
        prefix = cache.get(ticker, "NASDAQ:") 
        symbols_to_query.append(f"{prefix}{ticker}")

    if updated_radar_pre:
        with open(RADAR_FILE, 'w') as f:
            json.dump(radar, f, indent=2)

    if not symbols_to_query:
        print("NO_REPLY")
        return

    url = f"https://{RAPIDAPI_HOST}/api/quote/batch"
    headers = {
        "content-type": "application/json",
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    
    all_items = []
    chunk_size = 10
    
    for i in range(0, len(symbols_to_query), chunk_size):
        chunk = symbols_to_query[i:i + chunk_size]
        try:
            res = requests.post(url, json={"symbols": chunk}, headers=headers, timeout=10).json()
            if res.get("success") and "data" in res and "data" in res["data"]:
                all_items.extend(res["data"]["data"])
        except Exception:
            continue

    alerts = []
    updated = False

    if all_items:
        for item in all_items:
            if not item.get("success") or item.get("data", {}).get("current_session") != "market":
                continue
            
            symbol = item.get("symbol", "")
            ticker = symbol.split(":")[1] if ":" in symbol else symbol
            
            if ticker not in radar:
                continue
                
            price_data = item.get("data", {})
            current_price = price_data.get("lp") or price_data.get("bid") or price_data.get("ask")
            
            if not current_price:
                continue
                
            state = radar[ticker]
            highest = state["highest_seen_price"]
            drop = state["trailing_drop_amount"]
            
            # 1. Check for new high-water mark
            if current_price > highest:
                state["highest_seen_price"] = current_price
                new_theoretical_floor = round(current_price - drop, 2)
                if new_theoretical_floor > state.get("current_floor", 0.0):
                    state["current_floor"] = new_theoretical_floor
                state["last_updated"] = datetime.now().isoformat()
                updated = True
            
            current_floor = state.get("current_floor", highest - drop)
            
            # 2. Check for breach
            if current_price < current_floor:
                if ticker in ["SGOV", "BIL", "SHV"]:
                    state["status"] = "aborted_cash_shield"
                    updated = True
                    continue

                # Log to execution queue using exclusive file locking
                import fcntl
                import time
                from datetime import datetime
                
                os.makedirs(os.path.dirname("/root/.openclaw/workspace/memory/execution_queue.json"), exist_ok=True)
                with open("/root/.openclaw/workspace/memory/execution_queue.json", "a+") as eq_f:
                    fcntl.flock(eq_f, fcntl.LOCK_EX)
                    eq_f.seek(0)
                    try:
                        content = eq_f.read()
                        exec_queue = json.loads(content) if content.strip() else {}
                    except:
                        exec_queue = {}
                    
                    order_id = f"auto_{int(time.time())}_{ticker}"
                    exec_queue[order_id] = {
                        "timestamp": datetime.now().isoformat(),
                        "action": "SELL",
                        "order_type": "TRAILING_STOP",
                        "ticker": ticker,
                        "shares": state.get("shares", 1), # Default to 1 if not specified in radar
                        "execution_price": current_price,
                        "source": "1-Minute Intraday Trailing Sniper",
                        "status": "pending"
                    }
                    
                    eq_f.seek(0)
                    eq_f.truncate()
                    json.dump(exec_queue, eq_f, indent=2)
                
                state["status"] = "breached"
                updated = True

    if updated:
        with open(RADAR_FILE, 'w') as f:
            json.dump(radar, f, indent=2)

    # Snipers work in the dark. The Accountant sends the receipt.
    print("NO_REPLY")

if __name__ == "__main__":
    main()