import json
import urllib.request
import urllib.error
import os

TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

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
            if data:
                return data[0]['hashValue']
    except Exception as e:
        print(f"Error fetching account hash: {e}")
    return None

def preview_order(action, ticker, quantity, order_type="MARKET", price=None):
    result = {
        "sent_payload": {},
        "schwab_response": {}
    }
    
    token = get_access_token()
    if not token:
        result["schwab_response"] = {"error": "Could not load Access Token locally."}
        return result

    account_hash = get_account_hash(token)
    if not account_hash:
        result["schwab_response"] = {"error": "Could not retrieve account hash locally."}
        return result

    url = f"https://api.schwabapi.com/trader/v1/accounts/{account_hash}/previewOrder"
    
    payload = {
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": action.upper(),
                "quantity": int(quantity),
                "instrument": {
                    "assetType": "EQUITY",
                    "symbol": ticker.upper()
                }
            }
        ]
    }

    if order_type.upper() in ["LIMIT", "STOP_LOSS", "TRAILING_STOP_LIMIT"]:
        payload["orderType"] = "LIMIT"
        if price is None:
            result["schwab_response"] = {"error": "LIMIT orders require a price."}
            return result
        payload["price"] = f"{float(price):.2f}"
    else:
        payload["orderType"] = "MARKET"

    result["sent_payload"] = payload

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req) as response:
            resp_body = response.read().decode()
            if resp_body:
                result["schwab_response"] = json.loads(resp_body)
            else:
                result["schwab_response"] = {"status": response.getcode(), "message": "Preview OK (No body returned)"}
    except urllib.error.HTTPError as e:
        try:
            result["schwab_response"] = json.loads(e.read().decode())
        except:
            result["schwab_response"] = {"error_code": e.code, "message": str(e)}
    except Exception as e:
        result["schwab_response"] = {"error": str(e)}

    return result

if __name__ == "__main__":
    # Quick standalone test if executed directly
    print("Testing Preview Order for 1 AAPL Limit Buy at 303.49...")
    res = preview_order("BUY", "AAPL", 1, "LIMIT", 303.49)
    print("SENT PAYLOAD:")
    print(json.dumps(res["sent_payload"], indent=2))
    print("\nSCHWAB RESPONSE:")
    print(json.dumps(res["schwab_response"], indent=2))
