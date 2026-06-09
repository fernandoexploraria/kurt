import json
import sys
from tradingview_ta import TA_Handler, Interval

def get_detailed_ta(ticker, exchange):
    try:
        handler = TA_Handler(
            symbol=ticker,
            exchange=exchange,
            screener="america",
            interval=Interval.INTERVAL_1_DAY
        )
        analysis = handler.get_analysis()
        
        # Extended metrics for Fer's conviction report
        return {
            "ticker": ticker,
            "exchange": exchange,
            "summary": analysis.summary,
            "indicators": {
                "RSI": analysis.indicators.get("RSI"),
                "MACD": analysis.indicators.get("MACD.macd"),
                "MACD_signal": analysis.indicators.get("MACD.signal"),
                "EMA10": analysis.indicators.get("EMA10"),
                "EMA20": analysis.indicators.get("EMA20"),
                "SMA50": analysis.indicators.get("SMA50"),
                "SMA200": analysis.indicators.get("SMA200"),
                "ADX": analysis.indicators.get("ADX"),
                "CCI": analysis.indicators.get("CCI20"),
                "Stoch_K": analysis.indicators.get("Stoch.K"),
                "Stoch_D": analysis.indicators.get("Stoch.D")
            }
        }
    except Exception as e:
        return {"error": str(e)}

# Focusing specifically on FCX
result = get_detailed_ta("FCX", "NYSE")
print(json.dumps(result, indent=2))
