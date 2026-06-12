#!/usr/bin/env python3
import json
import os
import argparse
import subprocess
import sys
import re

PENDING_ORDERS_FILE = "/root/.openclaw/workspace/memory/pending_orders.json"
LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"

def run_gog(command):
    full_cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets {command} --json"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            return json.loads(result.stdout)
        except Exception as e:
            print(f"Error parsing GOG output: {e}")
            return None
    else:
        print(f"GOG API Error: {result.stderr}")
        return None

def fetch_position_data(ticker):
    # Fetch Positions sheet avoiding header row 1
    data = run_gog(f'get {LIVE_SHEET_ID} "Positions!A2:L100"')
    if not data or "values" not in data:
        return None
    
    for row in data["values"]:
        if len(row) > 0 and row[0].upper() == ticker.upper():
            try: target_price = float(row[6].replace('$', '').replace(',', '')) if len(row) > 6 and row[6].strip() else None
            except ValueError: target_price = None
                
            try: stop_loss = float(row[10].replace('$', '').replace(',', '')) if len(row) > 10 and row[10].strip() else None
            except ValueError: stop_loss = None
                
            try: shares = int(row[1].replace(',', '')) if len(row) > 1 and row[1].strip() else 0
            except ValueError: shares = 0
                
            return {"target_price": target_price, "stop_loss": stop_loss, "shares": shares}
    return None

def fetch_watchlist_data(ticker):
    data = run_gog(f'get {LIVE_SHEET_ID} "Watchlist!A2:H100"')
    if not data or "values" not in data:
        return None
    
    for row in data["values"]:
        if len(row) > 0 and row[0].upper() == ticker.upper():
            try: entry_price = float(row[3].replace('$', '').replace(',', '')) if len(row) > 3 and row[3].strip() else None
            except ValueError: entry_price = None
                
            shares = None
            if len(row) > 6 and row[6].strip():
                match = re.search(r'Buy\s+(\d+)\s+Shares', row[6], re.IGNORECASE)
                if match:
                    shares = int(match.group(1))
                    
            return {"entry_price": entry_price, "shares": shares}
    return None

def main():
    parser = argparse.ArgumentParser(description="Safely inject a trap (limit/stop order) into pending_orders.json")
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol")
    parser.add_argument("--action", required=True, choices=["BUY", "SELL"], help="BUY or SELL")
    parser.add_argument("--shares", type=int, help="Number of shares (Optional. Auto-fetches if omitted).")
    parser.add_argument("--price", type=float, help="Explicit limit/stop price. (Optional. Auto-fetches if omitted).")
    parser.add_argument("--tif", default="GTC", choices=["GTC", "DAY"], help="Time in force (GTC or DAY)")
    parser.add_argument("--intent", default="ENTRY", choices=["ENTRY", "TAKE_PROFIT", "STOP_LOSS"], help="Trap intent type")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing trap for this ticker")
    
    args = parser.parse_args()

    ticker = args.ticker.upper()
    action = args.action.upper()
    shares = args.shares
    price = args.price
    intent = args.intent.upper()
    tif = args.tif.upper()

    # Smart Fetching Logic
    if action == "SELL":
        if price is None or shares is None:
            print(f"[*] Fetching live position data for {ticker} from Google Sheets...")
            pos_data = fetch_position_data(ticker)
            if not pos_data:
                print(f"[!] Error: Could not find active position data for {ticker} in the spreadsheet.")
                sys.exit(1)
                
            if shares is None:
                if not pos_data["shares"] or pos_data["shares"] <= 0:
                    print(f"[!] Error: You do not own any shares of {ticker} to sell.")
                    sys.exit(1)
                shares = pos_data["shares"]
                print(f"[*] Auto-fetched Shares to Sell: {shares} (Total Position)")
                
            if price is None:
                if intent == "TAKE_PROFIT":
                    if not pos_data["target_price"]:
                        print(f"[!] Error: No 'Target Price' set in spreadsheet for {ticker}.")
                        sys.exit(1)
                    price = pos_data["target_price"]
                    print(f"[*] Auto-fetched Target Price: ${price:.2f}")
                elif intent == "STOP_LOSS":
                    if not pos_data["stop_loss"]:
                        print(f"[!] Error: No 'Floor (Stop-Loss)' set in spreadsheet for {ticker}.")
                        sys.exit(1)
                    price = pos_data["stop_loss"]
                    print(f"[*] Auto-fetched Stop-Loss Price: ${price:.2f}")
                else:
                    print("[!] Error: You must specify a --price for SELL ENTRY orders.")
                    sys.exit(1)

    elif action == "BUY":
        if price is None or shares is None:
            print(f"[*] Fetching live watchlist data for {ticker} from Google Sheets...")
            wl_data = fetch_watchlist_data(ticker)
            if not wl_data:
                print(f"[!] Error: Could not find {ticker} on the Watchlist.")
                sys.exit(1)
                
            if price is None:
                if not wl_data["entry_price"]:
                    print(f"[!] Error: No Entry Price calculated for {ticker} on the Watchlist.")
                    sys.exit(1)
                price = wl_data["entry_price"]
                print(f"[*] Auto-fetched Entry Price: ${price:.2f}")
                
            if shares is None:
                if not wl_data["shares"]:
                    print(f"[!] Error: Could not parse Share Count from Notes column for {ticker}.")
                    sys.exit(1)
                shares = wl_data["shares"]
                print(f"[*] Auto-fetched Share Allocation: {shares} Shares")

    # Load Pending Orders securely
    try:
        with open(PENDING_ORDERS_FILE, "r") as f:
            pending = json.load(f)
            if not isinstance(pending, dict):
                print("[!] Error: pending_orders.json is corrupted (not a dictionary).")
                sys.exit(1)
    except (FileNotFoundError, json.JSONDecodeError):
        pending = {}

    # Duplicate check protection
    if ticker in pending and not args.overwrite:
        print(f"[!] Error: A trap already exists for {ticker}. Use --overwrite to replace it.")
        sys.exit(1)

    final_action = "STOP_LOSS" if intent == "STOP_LOSS" else action
    
    order_payload = {
        "target_price": float(price),
        "type": tif,
        "status": "waiting",
        "action": final_action,
        "shares": shares
    }

    pending[ticker] = order_payload

    with open(PENDING_ORDERS_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    print(f"[+] SUCCESS: Trap safely set for {ticker} -> {final_action} {shares} shares @ ${price:.2f} ({tif} | {intent})")

if __name__ == "__main__":
    main()
