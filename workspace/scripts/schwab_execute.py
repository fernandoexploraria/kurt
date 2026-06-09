import json
import urllib.request
import urllib.error
import sys
import os

TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"
DRY_RUN = True  # 🛑 SAFETY SWITCH: Set to False to route live orders to Schwab

def get_access_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            tokens = json.load(f)
            return tokens.get("access_token")
    except:
        return None

def get_account_hash(token):
    url = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data[0]['hashValue']  # Grabs the first account
    except Exception as e:
        print(f"Error fetching account hash: {e}")
        return None

def place_order(action, ticker, quantity, order_type="MARKET", price=None):
    token = get_access_token()
    if not token:
        print("Error: Could not load Access Token.")
        return False

    account_hash = get_account_hash(token)
    if not account_hash:
        print("Error: Could not retrieve account hash.")
        return False

    url = f"https://api.schwabapi.com/trader/v1/accounts/{account_hash}/orders"
    
    # 1. Build the exact JSON payload
    payload = {
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": action.upper(),  # 'BUY' or 'SELL'
                "quantity": int(quantity),
                "instrument": {
                    "assetType": "EQUITY",
                    "symbol": ticker.upper()
                }
            }
        ]
    }

    if order_type.upper() == "LIMIT":
        payload["orderType"] = "LIMIT"
        if price is None:
            print("Error: LIMIT orders require a price.")
            return False
        payload["price"] = f"{float(price):.2f}"
    else:
        payload["orderType"] = "MARKET"

    print("\n" + "="*50)
    print(f"🚀 SCHWAB EXECUTION ENGINE | DRY_RUN = {DRY_RUN}")
    print("="*50)

    # 2. Safety Check (DRY RUN)
    if DRY_RUN:
        print("🛑 TRADE ABORTED: DRY_RUN is currently ENABLED.")
        print(f"Target URL: {url.split(account_hash)[0]}[ACCOUNT_HASH]/orders")
        print("\n📦 GENERATED JSON PAYLOAD:")
        print(json.dumps(payload, indent=2))
        print("="*50 + "\n")
        return True

    # 3. Live Execution
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req) as response:
            print(f"✅ LIVE TRADE EXECUTED!")
            print(f"Status Code: {response.getcode()}")
            
            # Schwab usually returns 201 Created and the location of the order in the headers
            order_url = response.headers.get('location')
            if order_url:
                order_id = order_url.split('/')[-1]
                print(f"Order ID: {order_id}")
            return True
            
    except urllib.error.HTTPError as e:
        print(f"❌ API Error ({e.code}): {e.read().decode()}")
        return False
    except Exception as e:
        print(f"❌ Execution failed: {e}")
        return False

if __name__ == "__main__":
    # Example test usage from CLI
    if len(sys.argv) > 4:
        action = sys.argv[1]
        ticker = sys.argv[2]
        qty = sys.argv[3]
        o_type = sys.argv[4]
        price = sys.argv[5] if len(sys.argv) > 5 else None
        place_order(action, ticker, qty, o_type, price)
    else:
        # Default test: The AAPL Limit Buy
        place_order("BUY", "AAPL", 1, "LIMIT", 303.49)
