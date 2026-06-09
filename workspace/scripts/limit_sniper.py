import json
import os
import requests

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

ORDERS_FILE = "/root/.openclaw/workspace/memory/pending_orders.json"
CACHE_FILE = "/root/.openclaw/workspace/memory/exchange_cache.json"
SHIELD_FILE = "/root/.openclaw/workspace/memory/quiver_shield.json"

def main():
    from datetime import datetime, time
    now = datetime.now().time()
    if not (time(7, 30) <= now <= time(13, 55)):
        print("NO_REPLY")
        return

    if not os.path.exists(ORDERS_FILE):
        print("NO_REPLY")
        return
        
    with open(ORDERS_FILE, 'r') as f:
        try:
            orders = json.load(f)
        except:
            print("NO_REPLY")
            return

    shield = {}
    if os.path.exists(SHIELD_FILE):
        with open(SHIELD_FILE, 'r') as f:
            try:
                shield = json.load(f)
            except:
                pass

            
    if not orders:
        print("NO_REPLY")
        return

    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            try:
                cache = json.load(f)
            except:
                pass

    symbols_to_query = []
    waiting_orders = False
    for ticker, data in orders.items():
        if data.get("status") == "waiting":
            waiting_orders = True
            # Check cache for prefix, default to NASDAQ
            prefix = cache.get(ticker, "NASDAQ:") 
            symbols_to_query.append(f"{prefix}{ticker}")

    if not waiting_orders or not symbols_to_query:
        print("NO_REPLY")
        return

    url = f"https://{RAPIDAPI_HOST}/api/quote/batch"
    headers = {
        "content-type": "application/json",
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    
    try:
        res = requests.post(url, json={"symbols": symbols_to_query}, headers=headers, timeout=10).json()
    except Exception as e:
        print("NO_REPLY")
        return

    alerts = []
    updated = False

    if res.get("success") and "data" in res and "data" in res["data"]:
        for item in res["data"]["data"]:
            if not item.get("success") or item.get("data", {}).get("current_session") != "market":
                continue
            
            symbol = item.get("symbol", "")
            ticker = symbol.split(":")[1] if ":" in symbol else symbol
            
            if ticker not in orders:
                continue
                
            price_data = item.get("data", {})
            current_price = price_data.get("lp") or price_data.get("bid") or price_data.get("ask")
            
            if not current_price:
                continue
                
            order = orders[ticker]
            if order.get("status") == "waiting":
                target_price = order.get("target_price", 0)
                # The trap!
                action = order.get("action", "BUY")
                if (action == "BUY" and current_price <= target_price) or (action == "SELL" and current_price >= target_price) or (action == "STOP_LOSS" and current_price <= target_price):
                    order_type = order.get("type", "DAY")
                    
                    if action == "BUY":
                        shield_data = shield.get(ticker, {})
                        conviction = shield_data.get("score", 50)
                        if conviction < 50:
                            reason = shield_data.get("reasoning", "Unknown")
                            print(f"🚨 **TRADE ABORTED:** {ticker} dropped to ${current_price:.2f}, but Quiver Conviction Score is {conviction} (< 50). Shield activated. ({reason})")
                            order["status"] = "aborted"
                            updated = True
                            continue

                    if action == "SELL":
                        msg = f"🚨 **SELL LIMIT REACHED:** {ticker} spiked to ${current_price:.2f}. Your {order_type} sell limit at ${target_price:.2f} was triggered!"
                    elif action == "STOP_LOSS":
                        msg = f"🛡️ **STOP-LOSS TRIGGERED:** {ticker} has broken the floor and dropped to ${current_price:.2f}. Your {order_type} stop-loss at ${target_price:.2f} was breached! "
                    else:
                        msg = f"🚨 **BUY LIMIT REACHED:** {ticker} dropped to ${current_price:.2f}. Your {order_type} buy limit trap at ${target_price:.2f} was triggered! "
                    # Log to execution queue
                    import time
                    from datetime import datetime
                    try:
                        with open("/root/.openclaw/workspace/memory/execution_queue.json", "r") as eq_f:
                            exec_queue = json.load(eq_f)
                    except:
                        exec_queue = {}
                    
                    order_id = f"auto_{int(time.time())}_{ticker}"
                    exec_queue[order_id] = {
                        "timestamp": datetime.now().isoformat(),
                        "action": "SELL" if action in ["SELL", "STOP_LOSS"] else "BUY",
                        "order_type": "STOP_LOSS" if action == "STOP_LOSS" else order_type,
                        "ticker": ticker,
                        "shares": order.get("shares", 1), # Default to 1 if not specified
                        "execution_price": current_price,
                        "source": "1-Minute Limit Sniper",
                        "status": "pending"
                    }
                    with open("/root/.openclaw/workspace/memory/execution_queue.json", "w") as eq_f:
                        json.dump(exec_queue, eq_f, indent=2)
                    
                    order["status"] = "triggered"
                    updated = True

    if updated:
        with open(ORDERS_FILE, 'w') as f:
            json.dump(orders, f, indent=2)

    # Snipers work in the dark. The Accountant sends the receipt.
    print("NO_REPLY")

if __name__ == "__main__":
    main()