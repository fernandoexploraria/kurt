import json
import sys
from tradingview_ta import TA_Handler, Interval

def get_ta_scan(ticker, exchange="NYSE"):
    try:
        handler = TA_Handler(
            symbol=ticker,
            exchange=exchange,
            screener="america",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis_daily = handler.get_analysis()
        
        handler.set_interval(Interval.INTERVAL_1_WEEK)
        analysis_weekly = handler.get_analysis()
        
        return {
            "daily": {
                "summary": analysis_daily.summary,
                "indicators": {
                    "RSI": analysis_daily.indicators["RSI"],
                    "MACD": analysis_daily.indicators["MACD.macd"],
                    "EMA20": analysis_daily.indicators["EMA20"],
                    "SMA50": analysis_daily.indicators["SMA50"],
                    "SMA200": analysis_daily.indicators["SMA200"],
                    "high": analysis_daily.indicators["high"],
                    "low": analysis_daily.indicators["low"]
                }
            },
            "weekly": {
                "summary": analysis_weekly.summary,
                "indicators": {
                    "RSI": analysis_weekly.indicators["RSI"],
                    "SMA50": analysis_weekly.indicators["SMA50"],
                    "SMA200": analysis_weekly.indicators["SMA200"]
                }
            }
        }
    except Exception as e:
        if exchange == "NYSE":
            return get_ta_scan(ticker, "NASDAQ")
        return {"error": str(e)}

if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "WM"
    result = get_ta_scan(ticker)
    print(json.dumps(result, indent=2))
