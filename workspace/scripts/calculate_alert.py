import json
import sys

try:
    with open('/root/.openclaw/workspace/aapl.json', 'r') as f:
        pass
except FileNotFoundError:
    pass

positions_json = """{
  "values": [
    ["Ticker", "Total Value"],
    ["CASH", "6.30"],
    ["AAPL", "925.89"],
    ["SCHD", "2558.81"],
    ["NEE", "3091.90"],
    ["SGOV", "3113.64"],
    ["AGNC", "2134.00"],
    ["TJX", "2313.90"],
    ["FCX", "548.73"],
    ["COST", "1903.34"],
    ["BRK.B", "1015.56"],
    ["WM", "1843.20"],
    ["MSFT", "390.49"],
    ["GNRC", "252.66"]
  ]
}"""
positions = json.loads(positions_json)["values"][2:]
valid_positions = []
for p in positions:
    if p[0] != "CASH" and p[0] != "SGOV":
        valid_positions.append((p[0], float(p[1])))

valid_positions.sort(key=lambda x: x[1], reverse=True)
top_5 = valid_positions[:5]
print("TOP5:" + ",".join([x[0] for x in top_5]))
