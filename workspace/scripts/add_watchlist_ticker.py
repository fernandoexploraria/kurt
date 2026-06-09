#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from datetime import datetime

SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"

def run_gog(cmd):
    full_cmd = f"GOG_ACCOUNT={ACCOUNT} {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error running gog command: {full_cmd}")
        print(result.stderr)
        sys.exit(1)
    return result.stdout.strip()

def get_existing_tickers():
    print("Fetching existing Watchlist tickers...")
    out = run_gog(f"gog sheets get {SHEET_ID} 'Watchlist!A:A' --json")
    data = json.loads(out)
    tickers = []
    for row in data.get('values', []):
        if row:
            tickers.append(str(row[0]).strip().upper())
    return set(tickers)

def main():
    parser = argparse.ArgumentParser(description="Add a stock to the Watchlist safely.")
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol (e.g., AAPL)")
    parser.add_argument("--sector", required=True, help="Sector or Category")
    parser.add_argument("--entry", required=True, type=float, help="Target Entry Price")
    parser.add_argument("--allocation", default="", help="Cash Allocation (e.g., '10.0%% Alloc')")
    parser.add_argument("--notes", default="", help="Notes or context for the trade")
    
    args = parser.parse_args()
    ticker = args.ticker.upper()
    
    existing = get_existing_tickers()
    if ticker in existing:
        print(f"[-] Action aborted: Ticker {ticker} is already in the Watchlist!")
        sys.exit(0)
        
    print(f"[+] Ticker {ticker} not found. Preparing to append...")
    
    # Constructing the row array
    # A: Ticker
    # B: Sector
    # C: Current Price Formula
    # D: Entry Price
    # E: % to Entry Formula
    # F: Cash Allocation
    # G: Notes
    # H: ATR (Leave empty, will be picked up by the calibrator later)
    # I: Date Added
    
    # Using INDIRECT to be completely agnostic of the row number
    current_price_formula = '=GOOGLEFINANCE(INDIRECT("A"&ROW()), "price")'
    percent_to_entry_formula = '=(INDIRECT("C"&ROW())-INDIRECT("D"&ROW()))/INDIRECT("D"&ROW())'
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    row_data = [
        ticker,
        args.sector,
        current_price_formula,
        args.entry,
        percent_to_entry_formula,
        args.allocation,
        args.notes,
        "",          # ATR placeholder
        today_str    # Date added
    ]
    
    # Convert to JSON string
    values_json = json.dumps([row_data])
    
    cmd = f"gog sheets append {SHEET_ID} 'Watchlist!A:I' --values-json '{values_json}' --input USER_ENTERED --insert INSERT_ROWS"
    run_gog(cmd)
    
    print(f"[+] Successfully added {ticker} to the Watchlist!")

if __name__ == "__main__":
    main()
