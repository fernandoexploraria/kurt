import json
import subprocess
from datetime import datetime
import time
import requests

import os

TEST_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"
headers = {"x-rapidapi-key": RAPIDAPI_KEY, "x-rapidapi-host": RAPIDAPI_HOST}

def run_subprocess(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0: return None
    return result.stdout.strip()

print("Re-establishing clean headers...")
run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Watchlist!A1:I1' --values-json '[[\"Ticker\", \"Sector\", \"Current Price\", \"Entry Price\", \"% to Entry\", \"Confidence (1-10)\", \"Notes\", \"ATR (14D)\", \"Last Updated\"]]' --input USER_ENTERED")

cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets get {TEST_SHEET_ID} 'Watchlist!A2:I50' --json"
out = run_subprocess(cmd)
data = json.loads(out)

for i, row in enumerate(data.get('values', [])):
    if len(row) > 0 and row[0].strip() and row[0].strip().upper() != "TICKER":
        ticker = row[0].strip()
        row_idx = i + 2
        print(f"Repairing {ticker} (Row {row_idx})...")
        
        # 1. Recover the Notes (they got jumbled between F and G)
        notes = ""
        confidence = "8" # default fallback
        
        if len(row) > 5 and str(row[5]).isdigit():
            confidence = row[5]
        if len(row) > 6 and str(row[6]).strip() and not str(row[6]).startswith("1900"):
            notes = str(row[6])
        elif len(row) > 5 and not str(row[5]).isdigit():
            # Sometimes notes got pushed into F
            notes = str(row[5])
            
        # 2. Recalculate pure ATR
        atr_val = 0.0
        prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:"]
        hist = None
        for prefix in prefixes:
            url = f"https://{RAPIDAPI_HOST}/api/price/{prefix}{ticker}?range=16&timeframe=D"
            try:
                res = requests.get(url, headers=headers).json()
                if res.get("success"):
                    hist = res["data"]["history"]
                    break
            except: pass
            time.sleep(0.2)
            
        if hist and len(hist) > 1:
            trs = []
            for j in range(1, len(hist)):
                h = hist[j]["max"]
                l = hist[j]["min"]
                pc = hist[j-1]["close"]
                tr = max(h - l, abs(h - pc), abs(l - pc))
                trs.append(tr)
            atr_val = round(sum(trs[-14:]) / min(14, len(trs)), 2)

        # 3. Clean Write exactly to designated columns
        run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Watchlist!C{row_idx}' --values-json '[[\"=GOOGLEFINANCE(\\\"{ticker}\\\", \\\"price\\\")\"]]' --input USER_ENTERED")
        run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Watchlist!F{row_idx}' --values-json '[[\"{confidence}\"]]' --input USER_ENTERED")
        
        # Escape quotes for JSON string injection
        safe_notes = notes.replace('"', '\\"')
        run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Watchlist!G{row_idx}' --values-json '[[\"{safe_notes}\"]]' --input USER_ENTERED")
        
        run_subprocess(f"GOG_ACCOUNT={ACCOUNT} gog sheets update {TEST_SHEET_ID} 'Watchlist!H{row_idx}' --values-json '[[{atr_val}]]' --input USER_ENTERED")

print("Repair Complete. Dashboard pristine.")
