import json
import sys
from tradingview_ta import TA_Handler, Interval

def get_ta_score(ticker, exchange="NASDAQ"):
    try:
        handler = TA_Handler(
            symbol=ticker,
            exchange=exchange,
            screener="america",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        return {
            "summary": analysis.summary,
            "indicators": {
                "RSI": analysis.indicators["RSI"],
                "MACD": analysis.indicators["MACD.macd"],
                "EMA20": analysis.indicators["EMA20"],
                "SMA50": analysis.indicators["SMA50"],
                "SMA200": analysis.indicators["SMA200"]
            }
        }
    except Exception as e:
        # Try NYSE if NASDAQ fails
        if exchange == "NASDAQ":
            return get_ta_score(ticker, "NYSE")
        return {"error": str(e)}

tickers = ["AAPL", "MSFT", "AVGO", "NVDA", "CIBR", "AMZN", "TJX", "WM", "UNH", "META", "GOOGL", "FCX", "VRT", "SCHD", "COST", "ADI", "GXO", "ETN", "SDGR", "BRK.B", "ZETA"]
results = {}

for ticker in tickers:
    # Special case for Berkshire
    t = ticker.replace(".", "-") if ticker == "BRK.B" else ticker
    results[ticker] = get_ta_score(t)

print(json.dumps(results, indent=2))
