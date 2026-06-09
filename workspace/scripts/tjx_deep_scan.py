import json
import sys
from tradingview_ta import TA_Handler, Interval

def get_ta_tjx(ticker="TJX", exchange="NYSE"):
    try:
        handler = TA_Handler(
            symbol=ticker,
            exchange=exchange,
            screener="america",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        
        # Get multi-timeframe summary
        h_summary = TA_Handler(symbol=ticker, exchange=exchange, screener="america", interval=Interval.INTERVAL_1_HOUR).get_analysis().summary
        w_summary = TA_Handler(symbol=ticker, exchange=exchange, screener="america", interval=Interval.INTERVAL_1_WEEK).get_analysis().summary

        return {
            "summary": analysis.summary,
            "hourly_summary": h_summary,
            "weekly_summary": w_summary,
            "indicators": {
                "RSI": analysis.indicators.get("RSI"),
                "MACD": analysis.indicators.get("MACD.macd"),
                "MACD_Signal": analysis.indicators.get("MACD.signal"),
                "ADX": analysis.indicators.get("ADX"),
                "EMA20": analysis.indicators.get("EMA20"),
                "SMA50": analysis.indicators.get("SMA50"),
                "SMA200": analysis.indicators.get("SMA200"),
                "Stoch.K": analysis.indicators.get("Stoch.K"),
                "CCI": analysis.indicators.get("CCI20"),
                "Bollinger.Upper": analysis.indicators.get("BB.upper"),
                "Bollinger.Lower": analysis.indicators.get("BB.lower")
            }
        }
    except Exception as e:
        return {"error": str(e)}

result = get_ta_tjx()
print(json.dumps(result, indent=2))
