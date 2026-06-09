import json
import sys
from tradingview_ta import TA_Handler, Interval

def get_ta_scan(ticker, exchange="NASDAQ"):
    results = {}
    intervals = {
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS,
        "1d": Interval.INTERVAL_1_DAY,
        "1w": Interval.INTERVAL_1_WEEK
    }
    
    for label, interval in intervals.items():
        try:
            handler = TA_Handler(
                symbol=ticker,
                exchange=exchange,
                screener="america",
                interval=interval
            )
            analysis = handler.get_analysis()
            results[label] = {
                "summary": analysis.summary,
                "rsi": analysis.indicators.get("RSI"),
                "macd": analysis.indicators.get("MACD.macd"),
                "adx": analysis.indicators.get("ADX"),
                "mom": analysis.indicators.get("Mom"),
                "cci": analysis.indicators.get("CCI20")
            }
        except Exception as e:
            results[label] = {"error": str(e)}
            
    return results

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(json.dumps(get_ta_scan(ticker), indent=2))
