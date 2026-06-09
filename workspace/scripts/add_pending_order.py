import json

path = "/root/.openclaw/workspace/memory/pending_orders.json"
try:
    with open(path, "r") as f:
        orders = json.load(f)
except:
    orders = {}

orders["AAPL"] = {
    "target_price": 286.28,
    "type": "GTC",
    "status": "waiting",
    "action": "BUY",
    "shares": 10
}

with open(path, "w") as f:
    json.dump(orders, f, indent=2)

print("Order Added.")
