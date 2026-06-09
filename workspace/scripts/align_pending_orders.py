import json

path = "/root/.openclaw/workspace/memory/pending_orders.json"
try:
    with open(path, "r") as f:
        orders = json.load(f)
except:
    orders = {}

if "WM" in orders:
    orders["WM"]["target_price"] = 207.48
if "COST" in orders:
    orders["COST"]["target_price"] = 918.25
if "LLY" in orders:
    orders["LLY"]["target_price"] = 1025.11

with open(path, "w") as f:
    json.dump(orders, f, indent=2)

print("Orders Aligned.")
