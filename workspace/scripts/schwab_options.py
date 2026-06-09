import json
import urllib.request
import urllib.error
import urllib.parse
import sys
from datetime import datetime, timedelta

TOKENS_FILE = "/root/.openclaw/workspace/memory/schwab_tokens.json"

def get_access_token():
    try:
        with open(TOKENS_FILE, 'r') as f:
            return json.load(f).get("access_token")
    except:
        return None

def get_options_sentiment(symbol):
    token = get_access_token()
    if not token:
        return "Error: Could not load Access Token."

    # Look ahead 45 days
    now = datetime.now()
    from_date = now.strftime("%Y-%m-%d")
    to_date = (now + timedelta(days=45)).strftime("%Y-%m-%d")

    params = urllib.parse.urlencode({
        "symbol": symbol,
        "contractType": "ALL",
        "fromDate": from_date,
        "toDate": to_date
    })
    url = f"https://api.schwabapi.com/marketdata/v1/chains?{params}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            
            if data.get("status") != "SUCCESS":
                return f"Error: Schwab returned status {data.get('status')}"

            calls = data.get("callExpDateMap", {})
            puts = data.get("putExpDateMap", {})

            total_call_vol = 0
            total_put_vol = 0
            total_call_oi = 0
            total_put_oi = 0
            
            strike_oi_map = {}
            iv_sum = 0
            iv_count = 0

            # Parse Calls
            for date_key, strikes in calls.items():
                for strike, contracts in strikes.items():
                    for c in contracts:
                        oi = c.get("openInterest", 0)
                        iv = c.get("volatility", 0)
                        
                        total_call_oi += oi
                        
                        strike_float = float(strike)
                        strike_oi_map[strike_float] = strike_oi_map.get(strike_float, 0) + oi
                        
                        if iv and iv > 0 and iv != 999.0 and iv != -999.0:
                            iv_sum += iv
                            iv_count += 1

            # Parse Puts
            for date_key, strikes in puts.items():
                for strike, contracts in strikes.items():
                    for p in contracts:
                        oi = p.get("openInterest", 0)
                        iv = p.get("volatility", 0)
                        
                        total_put_oi += oi
                        
                        strike_float = float(strike)
                        strike_oi_map[strike_float] = strike_oi_map.get(strike_float, 0) + oi
                        
                        if iv and iv > 0 and iv != 999.0 and iv != -999.0:
                            iv_sum += iv
                            iv_count += 1

            if total_call_oi + total_put_oi == 0:
                return f"No open interest found for {symbol} in the next 45 days. Market may be illiquid."

            put_call_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else float('inf')
            avg_iv = iv_sum / iv_count if iv_count > 0 else 0

            max_oi_strike = max(strike_oi_map, key=strike_oi_map.get) if strike_oi_map else 0
            max_oi_value = strike_oi_map.get(max_oi_strike, 0)

            sentiment = "NEUTRAL"
            if put_call_ratio > 1.2:
                sentiment = "BEARISH (Heavy Put Open Interest/Hedging)"
            elif put_call_ratio < 0.8:
                sentiment = "BULLISH (Heavy Call Open Interest)"

            output = f"📊 Options Sentiment for {symbol} (Next 45 Days):\n"
            output += f"- Overall Sentiment: {sentiment}\n"
            output += f"- Put/Call OI Ratio: {put_call_ratio:.2f} (Puts: {total_put_oi:,} | Calls: {total_call_oi:,})\n"
            output += f"- Average Implied Volatility (IV): {avg_iv:.2f}%\n"
            output += f"- 'Magnet' Strike (Highest Open Interest): ${max_oi_strike:.2f} ({max_oi_value:,} total contracts)\n"
            
            return output

    except urllib.error.HTTPError as e:
        return f"API Error ({e.code}): {e.read().decode()}"
    except Exception as e:
        return f"Failed to fetch options data: {str(e)}"

if __name__ == "__main__":
    symbol = sys.argv[1].upper() if len(sys.argv) > 1 else "AAPL"
    print(get_options_sentiment(symbol))
