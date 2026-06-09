import json
import os

ORDERS_FILE = "/root/.openclaw/workspace/memory/pending_orders.json"

def main():
    if not os.path.exists(ORDERS_FILE):
        print("NO_REPLY")
        return
        
    with open(ORDERS_FILE, 'r') as f:
        try:
            orders = json.load(f)
        except:
            print("NO_REPLY")
            return
            
    if not orders:
        print("NO_REPLY")
        return

    updated = False
    cleaned_orders = {}

    for ticker, data in orders.items():
        if data.get("type") == "DAY":
            updated = True
        else:
            cleaned_orders[ticker] = data

    if updated:
        with open(ORDERS_FILE, 'w') as f:
            json.dump(cleaned_orders, f, indent=2)
        print("Janitor sweep complete: Expired DAY orders removed.")
    else:
        print("NO_REPLY")

if __name__ == "__main__":
    main()