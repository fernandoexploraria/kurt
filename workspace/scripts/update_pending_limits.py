import json

path = "/root/.openclaw/workspace/memory/pending_orders.json"
try:
    with open(path, "r") as f:
        orders = json.load(f)
except:
    orders = {}

# Updating strictly based on the current spreadsheet math
if "WM" in orders:
    orders["WM"]["target_price"] = 212.22
    orders["WM"]["shares"] = 2

if "COST" in orders:
    orders["COST"]["target_price"] = 925.15
    orders["COST"]["shares"] = 1

if "LLY" in orders:
    orders["LLY"]["target_price"] = 1051.61
    orders["LLY"]["shares"] = 1

if "AAPL" in orders:
    orders["AAPL"]["target_price"] = 303.49
    orders["AAPL"]["shares"] = 1

with open(path, "w") as f:
    json.dump(orders, f, indent=2)

print("Limits Updated.")
