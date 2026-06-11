import os
import json
import base64
import urllib.request
import urllib.parse
import re
import gzip

TOOLS_PATH = "/root/.openclaw/workspace/TOOLS.md"
TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

def get_schwab_creds():
    creds = {
        "key": os.environ.get("SCHWAB_APP_KEY"),
        "secret": os.environ.get("SCHWAB_APP_SECRET"),
        "callback": None
    }
    if os.path.exists(TOOLS_PATH):
        with open(TOOLS_PATH, 'r') as f:
            content = f.read()
            match_callback = re.search(r'SCHWAB_CALLBACK_URL\s*[→=:]\s*(https?://[a-zA-Z0-9_.-]+)', content)
            if match_callback: creds["callback"] = match_callback.group(1)
            
    if not creds["key"] or not creds["secret"]:
        print("Error: SCHWAB_APP_KEY or SCHWAB_APP_SECRET missing from environment variables.")
    return creds

def generate_auth_url(creds):
    print("\n" + "="*50)
    print("🚀 SCHWAB OAUTH2 HANDSHAKE: STEP 1")
    print("="*50)
    print("You need to physically log into Schwab to authorize this app.")
    
    auth_url = f"https://api.schwabapi.com/v1/oauth/authorize?client_id={creds['key']}&redirect_uri={creds['callback']}"
    
    print("\n1️⃣ Click this exact link and log in to Schwab:")
    print(f"\n   {auth_url}\n")
    print("2️⃣ Schwab will redirect you to a broken page (https://127.0.0.1/?code=...)")
    print("3️⃣ Copy that ENTIRE URL from your browser's address bar and give it to Kurt.")
    print("="*50 + "\n")

def exchange_code_for_tokens(creds, auth_code):
    print("\n⏳ Exchanging Auth Code for permanent tokens...")
    
    url = "https://api.schwabapi.com/v1/oauth/token"
    
    # Schwab requires Basic Auth header with base64 encoded "key:secret"
    auth_string = f"{creds['key']}:{creds['secret']}"
    b64_auth = base64.b64encode(auth_string.encode('ascii')).decode('ascii')
    
    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": creds["callback"]
    }).encode('ascii')
    
    req = urllib.request.Request(url, headers=headers, data=data, method="POST")
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode())
            
            # Save tokens
            os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
            res_data["auth_timestamp"] = datetime.now().isoformat()
            res_data["warning_sent"] = False
            with open(TOKENS_FILE, 'w') as f:
                json.dump(res_data, f, indent=2)
                
            print("\n✅ SUCCESS! Tokens acquired and secured in memory/schwab_tokens.json")
            print(f"Access Token expires in: {res_data.get('expires_in', 1800)} seconds")
            
    except urllib.error.HTTPError as e:
        raw_body = e.read()
        try:
            body = gzip.decompress(raw_body).decode()
        except Exception:
            body = raw_body.decode(errors='replace')
        print(f"\n❌ API Error ({e.code}): {body}")
    except Exception as e:
        print(f"\n❌ Request failed: {e}")

if __name__ == "__main__":
    import sys
    creds = get_schwab_creds()
    
    if not creds["key"] or not creds["secret"]:
        print("Missing credentials in TOOLS.md")
        sys.exit(1)
        
    if len(sys.argv) > 1:
        # Step 2: The user passed us the URL they copied
        full_url = sys.argv[1]
        
        # Extract the code parameter from the URL
        if "code=" in full_url:
            raw_code = full_url.split("code=")[1].split("&")[0]
            # Schwab's code often has URL encoded characters (like %40) that need to be decoded
            auth_code = urllib.parse.unquote(raw_code)
            exchange_code_for_tokens(creds, auth_code)
        else:
            print("Error: Could not find 'code=' in the provided URL.")
    else:
        # Step 1: Generate the link
        generate_auth_url(creds)
