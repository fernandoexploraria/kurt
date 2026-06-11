import os
import json
import base64
import urllib.request
import urllib.parse
import gzip
import subprocess
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

    auth_ts_str = tokens.get("auth_timestamp")
    warning_sent = tokens.get("warning_sent", False)
    
    if auth_ts_str:
        try:
            auth_ts = datetime.fromisoformat(auth_ts_str)
            elapsed_days = (datetime.now() - auth_ts).total_seconds() / 86400.0
            
            if elapsed_days >= 6.0 and not warning_sent:
                alert_cmd = [
                    'curl', '-s', '-X', 'POST', 
                    f'https://api.telegram.org/bot{os.environ.get("TELEGRAM_BOT_TOKEN")}/sendMessage',
                    '-d', f'chat_id={os.environ.get("TELEGRAM_ALLOWED_USER_1")}',
                    '-d', 'text=⚠️ Fer, your Schwab token expires in 24 hours. To prevent trade failures, run this command now: python3 /root/.openclaw/workspace/scripts/schwab_auth.py'
                ]
                subprocess.run(alert_cmd)
                tokens["warning_sent"] = True
                with open(TOKENS_FILE, 'w') as f:
                    json.dump(tokens, f, indent=2)
        except Exception as e:
            print(f"Failed to process auth_timestamp: {e}")

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
        raw_body = e.read()
        try:
            body = gzip.decompress(raw_body).decode()
        except Exception:
            body = raw_body.decode(errors='replace')
        print(f"API Error during refresh ({e.code}): {body}")
        
        if "invalid_grant" in body or "expired" in body.lower():
            alert_cmd = [
                'curl', '-s', '-X', 'POST', 
                f'https://api.telegram.org/bot{os.environ.get("TELEGRAM_BOT_TOKEN")}/sendMessage',
                '-d', f'chat_id={os.environ.get("TELEGRAM_ALLOWED_USER_1")}',
                '-d', 'text=🚨 CRITICAL: Schwab Refresh Token has officially expired. Options and limit orders are offline. You must re-authenticate immediately by running: python3 /root/.openclaw/workspace/scripts/schwab_auth.py'
            ]
            subprocess.run(alert_cmd)
            
        return False
    except Exception as e:
        print(f"Refresh Request failed: {e}")
        return False

if __name__ == "__main__":
    refresh_token()
