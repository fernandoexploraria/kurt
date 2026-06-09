import json
import urllib.request
import urllib.parse
import subprocess
import time
from datetime import datetime, timedelta

LIVE_SHEET_ID = "1kjzfc6uEzBFtmNjlU1x3TVbHuWPgY7jnNce8mNTe66I"
ACCOUNT = "fernando@exploraria.ai"
TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

def get_schwab_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            return json.load(f).get("access_token")
    except:
        return None

def run_gog(cmd):
    full_cmd = f"GOG_ACCOUNT={ACCOUNT} gog sheets {cmd}"
    res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    try:
        return json.loads(res.stdout.strip())
    except:
        return None

def get_options_sentiment(symbol, token):
    now = datetime.now()
    from_date = now.strftime("%Y-%m-%d")
    to_date = (now + timedelta(days=45)).strftime("%Y-%m-%d")

    params = urllib.parse.urlencode({"symbol": symbol, "contractType": "ALL", "fromDate": from_date, "toDate": to_date})
    url = f"https://api.schwabapi.com/marketdata/v1/chains?{params}"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            if data.get("status") != "SUCCESS": return "N/A"

            total_call_oi = 0
            total_put_oi = 0
            
            for date_key, strikes in data.get("callExpDateMap", {}).items():
                for strike, contracts in strikes.items():
                    for c in contracts: total_call_oi += c.get("openInterest", 0)

            for date_key, strikes in data.get("putExpDateMap", {}).items():
                for strike, contracts in strikes.items():
                    for p in contracts: total_put_oi += p.get("openInterest", 0)

            if total_call_oi + total_put_oi == 0: return "Illiquid"
            
            pcr = total_put_oi / total_call_oi if total_call_oi > 0 else 999
            if pcr > 1.2: return f"BEARISH (PCR: {pcr:.2f})"
            elif pcr < 0.8: return f"BULLISH (PCR: {pcr:.2f})"
            else: return f"NEUTRAL (PCR: {pcr:.2f})"
    except:
        return "Error"

def main():
    token = get_schwab_token()
    print("Fetching Watchlist...")
    wl = run_gog(f'get {LIVE_SHEET_ID} "Watchlist!A:K" --json')
    print("Fetching Positions...")
    pos = run_gog(f'get {LIVE_SHEET_ID} "Positions!A:H" --json')
    
    candidates = {}

    # Parse Watchlist
    if wl and "values" in wl:
        for i, row in enumerate(wl["values"]):
            if i == 0 or not row: continue
            ticker = row[0].strip()
            if not ticker or ticker == "CASH": continue
            
            try:
                current_price = float(str(row[2]).replace('$', '').replace(',', ''))
                entry_price = float(str(row[3]).replace('$', '').replace(',', ''))
                perc_to_entry = float(str(row[4]).replace('%', ''))
                quiver = str(row[9]) if len(row) > 9 else "N/A"
                
                candidates[ticker] = {
                    "source": "Watchlist",
                    "price": current_price,
                    "target": entry_price,
                    "dist": perc_to_entry,
                    "quiver": quiver.split('|')[0].strip() if '|' in quiver else quiver
                }
            except:
                pass

    # Parse Positions (only 0 shares or ones we want to add to)
    if pos and "values" in pos:
        for i, row in enumerate(pos["values"]):
            if i < 2 or not row: continue
            ticker = row[0].strip()
            if not ticker or ticker == "CASH": continue
            
            shares = int(row[1])
            if shares == 0 and ticker not in candidates:
                try:
                    candidates[ticker] = {
                        "source": "Positions (0 Shares)",
                        "price": float(str(row[3]).replace('$', '').replace(',', '')),
                        "target": float(str(row[6]).replace('$', '').replace(',', '')),
                        "dist": float(str(row[7]).replace('%', '')),
                        "quiver": "N/A" # Positions doesn't have Quiver col natively
                    }
                except:
                    pass

    # Sort candidates by distance to entry (closest first)
    sorted_candidates = sorted(candidates.items(), key=lambda x: x[1]['dist'])
    
    print("\n--- TOP 5 MOST ACTIONABLE SETUPS ---")
    count = 0
    for ticker, data in sorted_candidates:
        if count >= 5: break
        
        # We only want to look at things that are pulling back TOWARDS our entry (positive distance, but small)
        # Or things that have just crossed below (negative distance)
        
        time.sleep(0.5)
        options_sent = get_options_sentiment(ticker, token)
        
        print(f"\n{count+1}. {ticker} ({data['source']})")
        print(f"   Price: ${data['price']} | Target: ${data['target']} ({data['dist']}% away)")
        print(f"   Quiver Score: {data['quiver']}")
        print(f"   Smart Money: {options_sent}")
        count += 1

if __name__ == "__main__":
    main()
