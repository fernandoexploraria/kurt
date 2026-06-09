import json
import urllib.request
import urllib.error

TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

def get_access_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            tokens = json.load(f)
            return tokens.get("access_token")
    except:
        return None

def get_quotes(symbols):
    token = get_access_token()
    if not token:
        print("Error: Could not load Access Token.")
        return

    # Schwab API requires symbols to be comma-separated
    symbol_string = ",".join(symbols)
    url = f"https://api.schwabapi.com/marketdata/v1/quotes?symbols={symbol_string}&fields=quote,fundamental"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            
            print("\n" + "="*40)
            print("🚀 SCHWAB LIVE MARKET DATA TEST")
            print("="*40)
            
            for symbol, quote_data in data.items():
                if "quote" in quote_data:
                    q = quote_data["quote"]
                    last_price = q.get("lastPrice", 0)
                    bid = q.get("bidPrice", 0)
                    ask = q.get("askPrice", 0)
                    vol = q.get("totalVolume", 0)
                    net_change = q.get("netChange", 0)
                    
                    print(f"\n📈 {symbol}")
                    print(f"  Last Price : ${last_price:.2f} ({net_change:+.2f})")
                    print(f"  Bid/Ask    : ${bid:.2f} / ${ask:.2f}")
                    print(f"  Volume     : {vol:,}")
                else:
                    print(f"\n⚠️ {symbol}: Data unavailable (Market might be closed or symbol invalid)")
                    
            print("\n" + "="*40)
            
    except urllib.error.HTTPError as e:
        print(f"API Error ({e.code}): {e.read().decode()}")
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    import sys
    # If symbols are passed via command line, use those. Otherwise default to AAPL and SPY
    symbols_to_fetch = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "SPY"]
    get_quotes(symbols_to_fetch)
