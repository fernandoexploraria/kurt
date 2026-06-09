import json
import urllib.request
import urllib.parse
from datetime import datetime

TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

def get_access_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            return json.load(f).get("access_token")
    except:
        return None

def fetch_movers(index):
    token = get_access_token()
    if not token:
        return []

    url = f"https://api.schwabapi.com/marketdata/v1/movers/{index}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    
    try:
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode())
            return data.get("screeners", [])
    except Exception as e:
        print(f"Error fetching movers for {index}: {e}")
        return []

def main():
    print(f"🔔 **MORNING MOVERS: The 8:35 AM Pulse** 🔔")
    print(f"*(NASDAQ Exchange - Live Schwab Order Flow)*\n")
    
    movers = fetch_movers("NASDAQ")
    if not movers:
        print("  No data available.")
        return

    # Filter out anything with 0 or missing data just in case
    valid_movers = [m for m in movers if m.get("netPercentChange") is not None]
    
    # Sort by percent change
    sorted_by_pct = sorted(valid_movers, key=lambda x: x.get("netPercentChange", 0))
    
    gainers = sorted_by_pct[-5:]  # Top 5 positive
    gainers.reverse() # Highest first
    
    losers = sorted_by_pct[:5]    # Top 5 negative (lowest first)

    print("🟢 **TOP 5 GAINERS (Pre/Open Surge):**")
    for g in gainers:
        symbol = g.get("symbol")
        price = g.get("lastPrice", 0)
        pct = g.get("netPercentChange", 0) * 100
        vol = g.get("volume", 0)
        print(f"  • **{symbol}**: ${price:.2f} (+{pct:.1f}%) | Vol: {vol:,}")
            
    print("\n🔴 **TOP 5 LOSERS (Pre/Open Dump):**")
    for l in losers:
        symbol = l.get("symbol")
        price = l.get("lastPrice", 0)
        pct = l.get("netPercentChange", 0) * 100
        vol = l.get("volume", 0)
        print(f"  • **{symbol}**: ${price:.2f} ({pct:.1f}%) | Vol: {vol:,}")
            
if __name__ == "__main__":
    main()
