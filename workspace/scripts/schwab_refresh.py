import os
import json
import base64
import urllib.request
import urllib.parse
from datetime import datetime

TOOLS_PATH = "/root/.openclaw/workspace/TOOLS.md"
TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

def get_schwab_creds():
    creds = {
        "key": os.environ.get("SCHWAB_APP_KEY"),
        "secret": os.environ.get("SCHWAB_APP_SECRET")
    }
    if not creds["key"] or not creds["secret"]:
        print("Error: SCHWAB_APP_KEY or SCHWAB_APP_SECRET missing from environment variables.")
    return creds

def refresh_token():
    if not os.path.exists(TOKENS_FILE):
        print("Error: No tokens file found.")
        return False
        
    with open(TOKENS_FILE, 'r') as f:
        tokens = json.load(f)
        
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("Error: No refresh token found in memory.")
        return False

    creds = get_schwab_creds()
    url = "https://api.schwabapi.com/v1/oauth/token"
    
    auth_string = f"{creds['key']}:{creds['secret']}"
    b64_auth = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
    
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }).encode('ascii')
    
    req = urllib.request.Request(url, headers=headers, data=data, method="POST")
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())
            
            # Keep the old refresh token if a new one wasn't provided
            if "refresh_token" not in res_data:
                res_data["refresh_token"] = refresh_token
                
            res_data["last_refreshed"] = datetime.now().isoformat()
                
            with open(TOKENS_FILE, 'w') as f:
                json.dump(res_data, f, indent=2)
                
            print(f"[{datetime.now().isoformat()}] Schwab Access Token successfully refreshed.")
            return True
            
    except urllib.error.HTTPError as e:
        print(f"API Error during refresh ({e.code}): {e.read().decode()}")
        return False
    except Exception as e:
        print(f"Refresh Request failed: {e}")
        return False

if __name__ == "__main__":
    refresh_token()
