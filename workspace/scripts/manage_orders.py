import json
import sys
import os

ORDERS_FILE = "/root/.openclaw/workspace/memory/pending_orders.json"

def load_orders():
    if os.path.exists(ORDERS_FILE):
        try:
            with open(ORDERS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_orders(orders):
    os.makedirs(os.path.dirname(ORDERS_FILE), exist_ok=True)
    with open(ORDERS_FILE, 'w') as f:
        json.dump(orders, f, indent=2)

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 manage_orders.py [add|remove|list] [args...]")
        return

    action = sys.argv[1].lower()
    orders = load_orders()

    if action == "add":
        if len(sys.argv) < 5:
            print("Usage: python3 manage_orders.py add <TICKER> <PRICE> <DAY|GTC>")
            return
        ticker = sys.argv[2].upper()
        try:
            price = float(sys.argv[3])
        except ValueError:
            print("Error: Price must be a number.")
            return
        order_type = sys.argv[4].upper()
        if order_type not in ["DAY", "GTC"]:
            print("Error: Type must be DAY or GTC.")
            return
        
        orders[ticker] = {
            "target_price": price,
            "type": order_type,
            "status": "waiting"
        }
        save_orders(orders)
        print(f"Success: Added {order_type} buy limit for {ticker} at ${price:.2f}.")

    elif action == "remove":
        if len(sys.argv) < 3:
            print("Usage: python3 manage_orders.py remove <TICKER>")
            return
        ticker = sys.argv[2].upper()
        if ticker in orders:
            del orders[ticker]
            save_orders(orders)
            print(f"Success: Removed order for {ticker}.")
        else:
            print(f"Error: No pending order found for {ticker}.")

    elif action == "list":
        if not orders:
            print("No pending orders.")
        else:
            print(json.dumps(orders, indent=2))
    else:
        print(f"Unknown action: {action}")

if __name__ == "__main__":
    main()