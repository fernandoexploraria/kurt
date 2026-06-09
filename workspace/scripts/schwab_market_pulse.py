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

def main():
    token = get_access_token()
    if not token:
        print("Error: Could not load Schwab token.")
        return

    # Using the urlencoded symbols to avoid shell escaping issues
    # $VIX, $TICK, SPY, QQQ
    symbols = "%24VIX,%24TICK,SPY,QQQ"
    url = f"https://api.schwabapi.com/marketdata/v1/quotes?symbols={symbols}"
    
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode())
    except Exception as e:
        print(f"Error fetching market pulse: {e}")
        return

    spy = data.get("SPY", {}).get("quote", {})
    qqq = data.get("QQQ", {}).get("quote", {})
    vix = data.get("$VIX", {}).get("quote", {})
    tick = data.get("$TICK", {}).get("quote", {})

    # Extract Data
    spy_price = spy.get("lastPrice", 0)
    spy_pct = spy.get("netPercentChange", 0)
    
    qqq_price = qqq.get("lastPrice", 0)
    qqq_pct = qqq.get("netPercentChange", 0)

    vix_price = vix.get("lastPrice", 0)
    vix_change = vix.get("netChange", 0)

    tick_price = tick.get("lastPrice", 0)

    # Contextual Logic
    vix_status = "Calm (Standard Environment)"
    if vix_price > 25:
        vix_status = "Elevated Fear (Hedging Active)"
    elif vix_price > 35:
        vix_status = "PANIC (Institutional De-Risking)"

    tick_status = "Orderly/Ranging"
    if tick_price >= 800:
        tick_status = "Heavy Algorithmic BUYING"
    elif tick_price <= -800:
        tick_status = "Heavy Algorithmic SELLING"
    elif tick_price > 0:
        tick_status = "Leaning Bullish"
    elif tick_price < 0:
        tick_status = "Leaning Bearish"

    market_trend = "🟢 GREEN" if spy_pct > 0 else "🔴 RED"

    print("🌤️ **MID-DAY MARKET WEATHER REPORT** 🌤️\n")
    
    print(f"**1. The Broad Market ({market_trend})**")
    print(f"   • **SPY (S&P 500):** ${spy_price:.2f} ({spy_pct:+.2f}%)")
    print(f"   • **QQQ (Nasdaq):** ${qqq_price:.2f} ({qqq_pct:+.2f}%)\n")

    print(f"**2. The Macro Fear Gauge ($VIX)**")
    print(f"   • **Current Level:** {vix_price:.2f} ({vix_change:+.2f})")
    print(f"   • **Status:** {vix_status}\n")

    print(f"**3. The Algorithmic Momentum ($TICK)**")
    print(f"   • **Current Tick:** {tick_price:+.0f}")
    print(f"   • **Status:** {tick_status}")

if __name__ == "__main__":
    main()
