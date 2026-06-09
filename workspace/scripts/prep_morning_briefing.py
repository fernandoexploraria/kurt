#!/usr/bin/env python3
import json
import os
import subprocess
from datetime import datetime
import time
import requests
import sys

# --- CONFIGURATION ---
LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY")
RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"

def run_subprocess(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()

def get_positions():
    print("Fetching Live Positions from Google Sheets...")
    # Fetching columns A through H to get Ticker, Current Price, and Target Price
    cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets get {LIVE_SHEET_ID} 'Positions!A4:H50' --json"
    out = run_subprocess(cmd)
    if not out:
        print("Failed to fetch positions.")
        return []
    
    data = json.loads(out)
    tickers = []
    for row in data.get('values', []):
        if len(row) > 0 and row[0].strip() and row[0].strip().upper() not in ["CASH", "TICKER"]:
            current_price = 0.0
            target_price = 0.0
            pct_to_target = 999.0
            
            if len(row) > 3 and row[3].strip():
                try: current_price = float(row[3].replace(',', ''))
                except: pass
            
            if len(row) > 6 and row[6].strip():
                try: target_price = float(row[6].replace(',', ''))
                except: pass
                
            if target_price > 0 and current_price > 0:
                pct_to_target = ((target_price - current_price) / current_price) * 100
                
            tickers.append({
                "ticker": row[0].strip(),
                "current_price": current_price,
                "target_price": target_price,
                "pct_to_target": pct_to_target
            })
    return tickers

def get_ta_trend(ticker):
    """Determines basic momentum using MACD and Moving Averages with Smart Retry Loop."""
    prefixes = ["NASDAQ:", "NYSE:", "AMEX:", "BATS:", "CRYPTO:", ""]
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": RAPIDAPI_HOST
    }
    
    for prefix in prefixes:
        url = f"https://{RAPIDAPI_HOST}/api/ta/{prefix}{ticker}/indicators"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            if data.get("success"):
                ind = data.get("data", {})
                
                # Check MACD crossover and RSI
                macd = ind.get("MACD.macd", 0)
                signal = ind.get("MACD.signal", 0)
                rsi = ind.get("RSI", 50)
                
                trend = "NEUTRAL"
                if macd > signal and rsi < 70:
                    trend = "BULLISH"
                elif macd < signal and rsi > 30:
                    trend = "BEARISH"
                elif rsi >= 70:
                    trend = "OVERBOUGHT (DANGER)"
                elif rsi <= 30:
                    trend = "OVERSOLD (BUY ZONE)"
                    
                return trend, rsi
        except:
            pass
        time.sleep(0.5)
    return "UNKNOWN", 50

def get_quiver_flags(ticker):
    """Flags heavy institutional shorting or congressional dumping."""
    flags = []

    # 1. Dark Pools (Institutional Danger)
    dpi_cmd = f"/root/.openclaw/workspace/quant_env/bin/python3 /root/.openclaw/workspace/skills/quiver-alpha/scripts/fetch.py darkpool {ticker}"
    dpi_out = run_subprocess(dpi_cmd)
    if dpi_out:
        try:
            dpi_data = json.loads(dpi_out)
            if isinstance(dpi_data, list) and len(dpi_data) > 0:
                latest_dpi = dpi_data[0].get("DPI", 0.5)
                if latest_dpi > 0.45:
                    flags.append("🚨 HIGH DARK POOL SHORTING")
        except:
            pass

    # 2. Congress (Insider Danger)
    congress_cmd = f"/root/.openclaw/workspace/quant_env/bin/python3 /root/.openclaw/workspace/skills/quiver/scripts/query_quiver.py congress --ticker {ticker}"
    congress_out = run_subprocess(congress_cmd)
    if congress_out:
        try:
            sales = congress_out.count('"Transaction":"Sale"')
            purchases = congress_out.count('"Transaction":"Purchase"')
            if sales > purchases * 2 and sales > 2:
                flags.append("🚨 HEAVY CONGRESSIONAL SELLING")
            elif purchases > sales * 2 and purchases > 2:
                flags.append("🟢 STRONG CONGRESSIONAL BUYING")
        except:
            pass

    return flags

def main():
    print("Starting Morning Briefing Data Scout...")
    tickers = get_positions()
    if not tickers:
        print("No active positions found.")
        return

    results = []

    for item in tickers:
        ticker = item['ticker']
        pct_to_target = item['pct_to_target']
        
        print(f"Scouting {ticker}...")
        
        trend, rsi = get_ta_trend(ticker)
        flags = get_quiver_flags(ticker)
        
        # Categorize the stock
        category = "STABLE"
        if pct_to_target <= 5.0:
            category = "HARVEST_ZONE"
        elif "BEARISH" in trend or "OVERBOUGHT" in trend or any("🚨" in f for f in flags):
            category = "DANGER_ZONE"
            
        results.append({
            "ticker": ticker,
            "pct": pct_to_target,
            "trend": trend,
            "rsi": round(rsi, 2),
            "flags": flags,
            "category": category
        })
        time.sleep(1)

    print("\n=========================================")
    print("      MORNING BRIEFING SCOUT REPORT      ")
    print("=========================================\n")
    
    # Print Harvest Zone
    print("🍎 THE HARVEST ZONE (<5% to Target):")
    harvest = [r for r in results if r['category'] == "HARVEST_ZONE"]
    if not harvest:
        print("  - None")
    for r in sorted(harvest, key=lambda x: x['pct']):
        print(f"  [{r['ticker']}] - {r['pct']:.2f}% to target. Trend: {r['trend']}. Flags: {', '.join(r['flags']) if r['flags'] else 'None'}")
        
    # Print Danger Zone
    print("\n⚠️ THE DANGER ZONE (Bearish TA or Insider Dumping):")
    danger = [r for r in results if r['category'] == "DANGER_ZONE"]
    if not danger:
        print("  - None")
    for r in danger:
        print(f"  [{r['ticker']}] - Trend: {r['trend']} (RSI: {r['rsi']}). Flags: {', '.join(r['flags']) if r['flags'] else 'None'}")
        
    # Print Stable
    print("\n🛡️ THE STABLE BEDROCK:")
    stable = [r for r in results if r['category'] == "STABLE"]
    if not stable:
        print("  - None")
    for r in stable:
        print(f"  [{r['ticker']}] - {r['pct']:.2f}% to target. Trend: {r['trend']}.")
        
    print("\nLLM INSTRUCTIONS:")
    print("Run web_search on the tickers in the HARVEST ZONE and DANGER ZONE only. Ignore the stable bedrock. Write the Angela-Ready summary.")

if __name__ == "__main__":
    main()