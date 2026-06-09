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

def get_account_numbers():
    token = get_access_token()
    if not token:
        print("Error: Could not load Access Token.")
        return None

    url = "https://api.schwabapi.com/trader/v1/accounts/accountNumbers"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            return data
    except urllib.error.HTTPError as e:
        print(f"API Error fetching account numbers ({e.code}): {e.read().decode()}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

def get_account_balances(hash_value):
    token = get_access_token()
    url = f"https://api.schwabapi.com/trader/v1/accounts/{hash_value}?fields=positions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"Failed to fetch balances: {e}")
        return None

if __name__ == "__main__":
    accounts = get_account_numbers()
    if accounts:
        print("\n" + "="*40)
        print("🚀 SCHWAB ACCOUNT RECONNAISSANCE")
        print("="*40)
        
        for acct in accounts:
            masked = acct.get('accountNumber')
            hash_val = acct.get('hashValue')
            print(f"\nDiscovered Account: {masked}")
            
            # Fetch balances for this account
            details = get_account_balances(hash_val)
            if details and "securitiesAccount" in details:
                sec = details["securitiesAccount"]
                acct_type = sec.get("type", "UNKNOWN")
                
                # Schwab has different keys depending on margin vs cash accounts
                bals = sec.get("currentBalances", {})
                cash_avail = bals.get("cashAvailableForTrading", 0)
                liquidation_val = bals.get("liquidationValue", 0)
                
                print(f"  Type : {acct_type}")
                print(f"  Total Value : ${liquidation_val:,.2f}")
                print(f"  Cash Avail  : ${cash_avail:,.2f}")
                
                positions = sec.get("positions", [])
                if positions:
                    print(f"\n  Active Positions ({len(positions)}):")
                    for p in positions:
                        symbol = p.get("instrument", {}).get("symbol", "UNKNOWN")
                        qty = p.get("longQuantity", 0)
                        market_val = p.get("marketValue", 0)
                        print(f"    - {symbol}: {qty} shares (${market_val:,.2f})")
                else:
                    print("  No active positions found.")
                    
        print("\n" + "="*40)
