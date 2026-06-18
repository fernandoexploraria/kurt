import json, requests, os
RAPIDAPI_KEY = ***"RAPIDAPI_KEY")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"
url = f"https://{RAPIDAPI_HOST}/api/quote/batch"
headers = {"content-type": "application/json", "x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}
res = requests.post(url, json={"symbols": ["NASDAQ:MSFT"]}, headers=headers, timeout=10).json()
print("Live price:", json.dumps(res, indent=2))
