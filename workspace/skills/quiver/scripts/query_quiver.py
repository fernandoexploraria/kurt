#!/root/.openclaw/workspace/quant_env/bin/python3
import argparse
import os
import sys
import json
import quiverquant
from datetime import datetime, timedelta

def get_quiver_token():
    # Try environment variable first
    token = os.environ.get("QUIVER_API_KEY")
    if token:
        return token
    
    # Try TOOLS.md or a local config file (simplified for this example)
    # In a real setup, we might parse TOOLS.md
    
    # Fallback to interactive prompt if allowed (unlikely in this context) or error
    print("Error: QUIVER_API_KEY environment variable not set.", file=sys.stderr)
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Query Quiver Quantitative API")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Congress Trading
    congress_parser = subparsers.add_parser("congress", help="Get congress trading data")
    congress_parser.add_argument("--ticker", help="Filter by ticker (e.g. AAPL)")
    congress_parser.add_argument("--politician", help="Filter by politician name")
    congress_parser.add_argument("--house", choices=["senate", "house"], help="Filter by house (senate/house)")
    
    # Lobbying
    lobbying_parser = subparsers.add_parser("lobbying", help="Get corporate lobbying data")
    lobbying_parser.add_argument("ticker", nargs="?", help="Ticker symbol")

    # Gov Contracts
    contracts_parser = subparsers.add_parser("contracts", help="Get government contracts")
    contracts_parser.add_argument("ticker", nargs="?", help="Ticker symbol")

    # Insiders
    insider_parser = subparsers.add_parser("insiders", help="Get insider transactions")
    insider_parser.add_argument("ticker", nargs="?", help="Ticker symbol")

    args = parser.parse_args()

    token = get_quiver_token()
    quiver = quiverquant.quiver(token)

    try:
        df = None
        if args.command == "congress":
            if args.politician:
                df = quiver.congress_trading(args.politician, politician=True)
            elif args.ticker:
                df = quiver.congress_trading(args.ticker)
            else:
                df = quiver.congress_trading()
                
            # Filter by house if specified (post-processing since API might not support it directly in one call)
            if args.house and df is not None:
                if "House" in df.columns: # Adjust column name based on actual API response
                     df = df[df["House"] == args.house] # Pseudo-code, need to verify exact column name
        
        elif args.command == "lobbying":
            if args.ticker:
                df = quiver.lobbying(args.ticker)
            else:
                df = quiver.lobbying()

        elif args.command == "contracts":
            if args.ticker:
                df = quiver.gov_contracts(args.ticker)
            else:
                df = quiver.gov_contracts()
                
        elif args.command == "insiders":
            # print(f"Drawing from:  https://api.quiverquant.com/beta/live/insiders?ticker={args.ticker}", file=sys.stderr)
            import requests
            url = f"https://api.quiverquant.com/beta/live/insiders?ticker={args.ticker}"
            headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                print(json.dumps(response.json()))
                return
            else:
                print(f"Error: {response.status_code} - {response.text}", file=sys.stderr)
                sys.exit(1)

        if df is not None:
            # Handle the case where df might be a Series or have only one row causing indexing issues
            if hasattr(df, "to_json"):
                try:
                    print(df.to_json(orient="records", date_format="iso"))
                except Exception:
                    # Fallback for Series or other pandas types that struggle with orient="records"
                    print(df.to_json(date_format="iso"))
            elif isinstance(df, dict):
                print(json.dumps(df))
            else:
                print("[]")
        else:
            print("[]") # Empty JSON array

    except Exception as e:
        print(f"Error querying Quiver API: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
