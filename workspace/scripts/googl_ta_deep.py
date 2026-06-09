import json
import sys
from tradingview_ta import TA_Handler, Interval

def analyze_ticker(ticker, exchange="NASDAQ"):
    intervals = {
        "1D": Interval.INTERVAL_1_DAY,
        "1W": Interval.INTERVAL_1_WEEK
    }
    
    results = {}
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
                "indicators": {
                    "RSI": analysis.indicators.get("RSI"),
                    "MACD": analysis.indicators.get("MACD.macd"),
                    "MACD_signal": analysis.indicators.get("MACD.signal"),
                    "ADX": analysis.indicators.get("ADX"),
                    "EMA20": analysis.indicators.get("EMA20"),
                    "SMA50": analysis.indicators.get("SMA50"),
                    "SMA200": analysis.indicators.get("SMA200"),
                    "Stoch.K": analysis.indicators.get("Stoch.K"),
                    "Bollinger.upper": analysis.indicators.get("BB.upper"),
                    "Bollinger.lower": analysis.indicators.get("BB.lower")
                }
            }
        except Exception as e:
            results[label] = {"error": str(e)}
            
    return results

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "GOOGL"
    print(json.dumps(analyze_ticker(ticker), indent=2))
