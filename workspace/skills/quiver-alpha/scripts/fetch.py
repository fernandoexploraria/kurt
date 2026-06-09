#!/usr/bin/env python3
import os
import sys
import json
import urllib.request
import urllib.error
import argparse

def get_quiver_token():
    token = os.environ.get("QUIVER_API_KEY")
    if token:
        return token
        
    print("Error: QUIVER_API_KEY not found in environment", file=sys.stderr)
    sys.exit(1)

def api_request(endpoint, token):
    url = f"https://api.quiverquant.com/beta/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    req = urllib.request.Request(url, headers=headers, method='GET')
    
    try:
        with urllib.request.urlopen(req) as response:
            content = response.read()
            if content:
                return json.loads(content)
            return []
    except urllib.error.HTTPError as e:
        print(f"API Error ({e.code}): {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request failed: {e}", file=sys.stderr)
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Quiver-Alpha: Direct API Alternative Data Engine")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # 1. Dark Pools / Off-Exchange
    darkpool_parser = subparsers.add_parser("darkpool", help="Get off-exchange (dark pool) activity")
    darkpool_parser.add_argument("ticker", help="Ticker symbol (e.g., NVDA)")
    
    # 2. Institutional Whales / Top Shareholders
    whales_parser = subparsers.add_parser("whales", help="Get top institutional shareholders")
    whales_parser.add_argument("ticker", help="Ticker symbol (e.g., COST)")
    
    # 3. Live Congress Pulse (Global)
    pulse_parser = subparsers.add_parser("pulse", help="Get the latest congressional trades across the entire market")
    
    # 4. SEC 13F Changes (Smart Money Moves)
    sec_parser = subparsers.add_parser("sec13f", help="Get recent SEC 13F fund changes")
    sec_parser.add_argument("ticker", help="Ticker symbol (e.g., MSFT)")
    
    # 5. Live Insiders
    insiders_parser = subparsers.add_parser("insiders", help="Get recent corporate insider trades")
    insiders_parser.add_argument("ticker", help="Ticker symbol (e.g., AAPL)")

    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)

    token = get_quiver_token()
    
    result = None
    
    if args.command == "darkpool":
        result = api_request(f"historical/offexchange/{args.ticker}", token)
    elif args.command == "whales":
        result = api_request(f"live/topshareholders/{args.ticker}", token)
    elif args.command == "pulse":
        result = api_request("live/congresstrading", token)
    elif args.command == "sec13f":
        result = api_request(f"live/sec13fchanges?ticker={args.ticker}&page_size=50", token)
    elif args.command == "insiders":
        result = api_request(f"live/insiders?ticker={args.ticker}&page_size=50", token)

    # Output formatted JSON
    if result is not None:
        print(json.dumps(result, indent=2))
    else:
        print("[]")

if __name__ == "__main__":
    main()